# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

import civiccast.egress.source_plan as source_plan_module
from civiccast.egress import resolver
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.models import (
    MAX_PLAYLIST_SUBCHAINS,
    CanonicalProfile,
    EgressConfig,
    EgressSinkSpec,
)
from civiccast.egress.source_plan import (
    DEFAULT_GSTREAMER_SOURCE_SEGMENT_SECONDS,
    PLAN_MIN_SECONDS,
    SCHEDULE_GAP_ABSORB_SECONDS,
    ScheduleSourcePlanProvider,
    SlateSourceGenerator,
    _escape_drawtext,
    build_slate_source_args,
    build_source_plan_from_schedule,
    gstreamer_source_segment_seconds_from_env,
    schedule_items_in_window,
)
from civiccast.schedule.models import ScheduleItemResponse, StaffAssetRow
from civiccast.stream._ffmpeg import FfmpegResult


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        slate_message="CivicCast is preparing the channel: please stand by.",
        canonical_profile=CanonicalProfile(width=640, height=360, video_bitrate_kbps=1200),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/out.ts")],
    )


def _schedule_item(
    *,
    asset_id: str = "council-meeting",
    channel_id: str = "gov",
    scheduled_at: datetime,
    duration_seconds: int = 1800,
    state: str = "published",
) -> ScheduleItemResponse:
    return ScheduleItemResponse(
        id=uuid4(),
        asset_id=asset_id,
        asset_title="Council Meeting",
        channel_id=channel_id,
        mode="premiere",
        state=state,
        scheduled_at=scheduled_at,
        duration_seconds=duration_seconds,
        notes=None,
        created_at=scheduled_at - timedelta(days=1),
    )


def _asset(path: Path, *, asset_id: str = "council-meeting") -> StaffAssetRow:
    return StaffAssetRow(
        asset_id=asset_id,
        title="Council Meeting",
        state="validated",
        file_path=str(path),
        duration_seconds=1800,
        trim_in_seconds=10,
        trim_out_seconds=120,
    )


def test_repeat_setting_changes_without_restarting_source_provider(tmp_path):
    start = datetime(2026, 10, 29, tzinfo=UTC)
    media = tmp_path / "program.mp4"
    media.write_bytes(b"fixture")
    item = _schedule_item(scheduled_at=start, duration_seconds=110)
    enabled = {"gov": False}
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _: [item],
        asset_resolver=lambda _: _asset(media),
        now_provider=lambda: start + timedelta(seconds=220),
        loop_schedule_provider=lambda channel: enabled[channel],
        media_duration_resolver=lambda _: 120.0,
    )
    assert provider("gov") is None
    enabled["gov"] = True
    assert provider("gov") is not None
    enabled["gov"] = False
    assert provider("gov") is None


def test_build_slate_source_args_uses_canonical_profile_and_escapes_message(
    tmp_path: Path,
) -> None:
    config = _config().model_copy(update={"slate_message": "Mayor's update: standby"})

    args = build_slate_source_args(
        output_path=tmp_path / "slate.ts",
        config=config,
        duration_seconds=30,
    )

    assert "color=c=0x1a2744:size=640x360:rate=30:duration=30" in args
    assert "1200k" in args
    assert "Mayor\\'s update\\: standby" in " ".join(args)
    assert args[-3:] == ["-f", "mpegts", str(tmp_path / "slate.ts")]


def test_build_slate_source_args_can_skip_drawtext(tmp_path: Path) -> None:
    args = build_slate_source_args(
        output_path=tmp_path / "slate.ts",
        config=_config(),
        duration_seconds=30,
        include_text=False,
    )

    assert "-vf" not in args
    assert "drawtext" not in " ".join(args)


class TestEscapeDrawtext:
    """Gate finding F-3: this is the ONE shared drawtext-escaping implementation.

    ``board_compositor.py`` and ``bulletin_filler.py`` both import this rather
    than keeping their own copy (previously two independent copies existed and
    had already drifted once in call order). These tests pin the exact
    metacharacter set both prior versions handled -- backslash, single quote,
    and colon -- plus the backslash-first ordering that keeps a
    later-introduced backslash from being re-escaped.
    """

    def test_backslash_is_doubled(self) -> None:
        assert _escape_drawtext("a\\b") == "a\\\\b"

    def test_single_quote_is_escaped(self) -> None:
        assert _escape_drawtext("Mayor's update") == "Mayor\\'s update"

    def test_colon_is_escaped(self) -> None:
        assert _escape_drawtext("18:30 meeting") == "18\\:30 meeting"

    def test_all_three_metacharacters_together_backslash_first(self) -> None:
        # If colon/quote escaping ran before backslash escaping, the
        # backslashes those steps introduce would get doubled again. Backslash
        # must run first so `\:` and `\'` survive as single backslashes.
        assert _escape_drawtext(r"Mayor's \ update: 5pm") == r"Mayor\'s \\ update\: 5pm"

    def test_plain_text_is_unchanged(self) -> None:
        assert _escape_drawtext("Community programming") == "Community programming"


def test_slate_source_generator_returns_source_plan(tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def runner(args: list[str]) -> FfmpegResult:
        captured["args"] = args
        Path(args[-1]).write_bytes(b"ts")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    generator = SlateSourceGenerator(
        work_dir=tmp_path,
        ffmpeg_runner=runner,
    )

    plan = generator(_config())

    assert plan.channel_id == "gov"
    assert plan.segments[0].label == "CivicCast slate"
    assert Path(plan.segments[0].path).name.startswith("slate-")
    assert Path(captured["args"][-1]).name.startswith(".")


def test_slate_source_generator_falls_back_to_plain_color(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(args)
        if len(calls) == 1:
            return FfmpegResult(returncode=1, stdout="", stderr="")
        Path(args[-1]).write_bytes(b"ts")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    generator = SlateSourceGenerator(work_dir=tmp_path, ffmpeg_runner=runner)

    plan = generator(_config())

    assert plan.segments[0].label == "CivicCast slate"
    assert "-vf" in calls[0]
    assert "-vf" not in calls[1]


def test_slate_source_generator_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    generator = SlateSourceGenerator(
        work_dir=tmp_path,
        ffmpeg_runner=lambda _args: FfmpegResult(returncode=1, stdout="", stderr="boom"),
    )

    with pytest.raises(SourcePrepareError, match="Could not generate"):
        generator(_config())


def test_build_source_plan_from_schedule_uses_current_local_media_with_trim(
    tmp_path: Path,
) -> None:
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[
            _schedule_item(scheduled_at=now),
            _schedule_item(
                asset_id="next-meeting",
                scheduled_at=now + timedelta(minutes=30),
                duration_seconds=1200,
            ),
        ],
        asset_resolver=lambda asset_id: _asset(media, asset_id=asset_id),
        now=now,
    )

    assert plan is not None
    assert plan.channel_id == "gov"
    assert plan.segments[0].path == str(media)
    assert plan.segments[0].duration_seconds == 110
    assert plan.segments[0].inpoint_seconds == 10
    assert plan.segments[0].outpoint_seconds == 120
    assert plan.segments[0].schedule_occurrence_start == now
    # D42: the trim window is 110s but the slot is 30 minutes, so this item
    # UNDER-FILLS its slot. The plan stops here -- the rest of the slot belongs
    # to the channel's fill policy (bulletins/slate), reached through the
    # daemon's FALLBACK_SLATE gap-replan. Before this fix the next item was
    # appended anyway and therefore started 110s in instead of 30 minutes in.
    assert [segment.label for segment in plan.segments] == ["Council Meeting"]


def test_gstreamer_preparation_horizon_clips_a_long_current_item(tmp_path: Path) -> None:
    """A multi-hour item must not become one multi-hour cold conform at start."""

    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[_schedule_item(scheduled_at=now, duration_seconds=14_400)],
        asset_resolver=lambda asset_id: _asset_of(
            media, duration_seconds=14_400, asset_id=asset_id
        ),
        now=now,
        max_segments=1,
        max_segment_seconds=60.0,
    )

    assert plan is not None
    assert len(plan.segments) == 1
    assert plan.segments[0].duration_seconds == 60.0
    assert plan.segments[0].inpoint_seconds is None
    assert plan.segments[0].outpoint_seconds is None


def test_gstreamer_preparation_horizon_preserves_join_in_progress(tmp_path: Path) -> None:
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)
    now = start + timedelta(seconds=20)

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[_schedule_item(scheduled_at=start, duration_seconds=300)],
        asset_resolver=lambda asset_id: _asset_of(media, duration_seconds=300, asset_id=asset_id),
        now=now,
        max_segments=1,
        max_segment_seconds=60.0,
    )

    assert plan is not None
    segment = plan.segments[0]
    assert segment.inpoint_seconds == 20.0
    assert segment.duration_seconds == 60.0
    assert segment.outpoint_seconds is None


def test_gstreamer_preparation_horizon_rejects_non_positive_values(tmp_path: Path) -> None:
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="max_segment_seconds"):
        build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now)],
            asset_resolver=lambda asset_id: _asset(media, asset_id=asset_id),
            now=now,
            max_segment_seconds=0,
        )


class TestGstreamerSourceSegmentSecondsEnvSpelling:
    """BETA.10 U03. ``gstreamer_source_segment_seconds_from_env`` read only the
    one-C ``CIVICAST_GSTREAMER_SOURCE_SEGMENT_SECONDS`` while the station's
    service registry (``HKLM\\SYSTEM\\CurrentControlSet\\Services\\
    CivicCastSupervisor``'s ``Environment``) sets the two-C
    ``CIVICCAST_GSTREAMER_SOURCE_SEGMENT_SECONDS=1800`` -- so the station
    setting was ignored.
    """

    _PRIMARY = "CIVICCAST_GSTREAMER_SOURCE_SEGMENT_SECONDS"
    _LEGACY = "CIVICAST_GSTREAMER_SOURCE_SEGMENT_SECONDS"
    _LOGGER = "civiccast.egress.source_plan"

    @pytest.fixture(autouse=True)
    def _fresh_one_time_latch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # raising=False so the same fixture also runs against the
        # pre-U03 module in the red-first demonstration, where this
        # latch does not exist yet.
        monkeypatch.setattr(source_plan_module, "_RENAMED_ENV_WARNED", set(), raising=False)

    def test_the_default_is_the_bounded_horizon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert DEFAULT_GSTREAMER_SOURCE_SEGMENT_SECONDS == 1800.0
        monkeypatch.delenv(self._PRIMARY, raising=False)
        monkeypatch.delenv(self._LEGACY, raising=False)
        assert (
            gstreamer_source_segment_seconds_from_env() == DEFAULT_GSTREAMER_SOURCE_SEGMENT_SECONDS
        )

    def test_the_registry_spelling_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """RED before this change: with only the two-C name set the reader
        returned the 1800s default rather than the 600 here."""

        monkeypatch.delenv(self._LEGACY, raising=False)
        monkeypatch.setenv(self._PRIMARY, "600")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert gstreamer_source_segment_seconds_from_env() == 600.0

        assert caplog.records == []

    def test_the_legacy_spelling_still_works_and_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv(self._PRIMARY, raising=False)
        monkeypatch.setenv(self._LEGACY, "600")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert gstreamer_source_segment_seconds_from_env() == 600.0
            assert gstreamer_source_segment_seconds_from_env() == 600.0

        deprecations = [record for record in caplog.records if self._LEGACY in record.getMessage()]
        assert len(deprecations) == 1, [record.getMessage() for record in caplog.records]
        assert self._PRIMARY in deprecations[0].getMessage()

    def test_the_registry_spelling_wins_and_both_values_are_named(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(self._LEGACY, "600")
        monkeypatch.setenv(self._PRIMARY, "900")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert gstreamer_source_segment_seconds_from_env() == 900.0

        conflicts = [record for record in caplog.records if "both set" in record.getMessage()]
        assert len(conflicts) == 1, [record.getMessage() for record in caplog.records]
        message = conflicts[0].getMessage()
        assert "900" in message and "600" in message

    @pytest.mark.parametrize("bad", ["bad", "-5", "0"])
    def test_an_invalid_value_falls_back_and_names_the_spelling_used(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
    ) -> None:
        monkeypatch.delenv(self._LEGACY, raising=False)
        monkeypatch.setenv(self._PRIMARY, bad)

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert (
                gstreamer_source_segment_seconds_from_env()
                == DEFAULT_GSTREAMER_SOURCE_SEGMENT_SECONDS
            )

        assert any(self._PRIMARY in record.getMessage() for record in caplog.records), [
            record.getMessage() for record in caplog.records
        ]


def test_scheduled_uncommitted_item_is_excluded_from_the_plan(tmp_path: Path) -> None:
    """Commit-to-Air gate (spec test a): a premiere still in ``scheduled``
    state (not yet approved via commit, and not auto-approved by
    autoschedule) must not air — the resolver only plays ``published``
    items."""
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[_schedule_item(scheduled_at=now, state="scheduled")],
        asset_resolver=lambda asset_id: _asset(media, asset_id=asset_id),
        now=now,
    )

    assert plan is None


class TestJoinInProgress:
    """CA-2: a (re)start mid-program rejoins the current item at the
    wall-clock offset instead of replaying it from the top and drifting
    the channel off its published log."""

    def _long_asset(self, path: Path, *, asset_id: str = "council-meeting") -> StaffAssetRow:
        return StaffAssetRow(
            asset_id=asset_id,
            title="Council Meeting",
            state="validated",
            file_path=str(path),
            duration_seconds=1800,
            trim_in_seconds=None,
            trim_out_seconds=None,
        )

    def test_restart_mid_program_offsets_into_the_current_item(self, tmp_path: Path) -> None:
        media = tmp_path / "council.ts"
        media.write_text("fake", encoding="utf-8")
        now = datetime(2026, 6, 5, 18, 10, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now - timedelta(minutes=10))],
            asset_resolver=lambda asset_id: self._long_asset(media, asset_id=asset_id),
            now=now,
        )

        assert plan is not None
        segment = plan.segments[0]
        assert segment.inpoint_seconds == 600
        assert segment.duration_seconds == 1200

    def test_offset_respects_an_existing_trim_window(self, tmp_path: Path) -> None:
        media = tmp_path / "council.ts"
        media.write_text("fake", encoding="utf-8")
        now = datetime(2026, 6, 5, 18, 1, tzinfo=UTC)
        trimmed = StaffAssetRow(
            asset_id="council-meeting",
            title="Council Meeting",
            state="validated",
            file_path=str(media),
            duration_seconds=1800,
            trim_in_seconds=10,
            trim_out_seconds=1200,
        )

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now - timedelta(minutes=1))],
            asset_resolver=lambda _asset_id: trimmed,
            now=now,
        )

        assert plan is not None
        segment = plan.segments[0]
        # 60s elapsed: playback resumes 60s into the TRIMMED window.
        assert segment.inpoint_seconds == 70
        assert segment.outpoint_seconds == 1200
        assert segment.duration_seconds == 1130

    def test_exhausted_media_falls_back_to_slate_for_the_slot_remainder(
        self, tmp_path: Path
    ) -> None:
        media = tmp_path / "council.ts"
        media.write_text("fake", encoding="utf-8")
        now = datetime(2026, 6, 5, 18, 10, tzinfo=UTC)
        # Media (trim window) is only 110s long; the slot is 30 minutes.
        # 10 minutes in, the program has fully aired: honest behavior is
        # slate (None) until the next item is due — never replaying the
        # program and never starting the next item early.
        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[
                _schedule_item(scheduled_at=now - timedelta(minutes=10)),
                _schedule_item(
                    asset_id="next-meeting",
                    scheduled_at=now + timedelta(minutes=20),
                    duration_seconds=1200,
                ),
            ],
            asset_resolver=lambda asset_id: _asset(media, asset_id=asset_id),
            now=now,
        )

        assert plan is None

    def test_on_time_start_is_unchanged(self, tmp_path: Path) -> None:
        media = tmp_path / "council.ts"
        media.write_text("fake", encoding="utf-8")
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now)],
            asset_resolver=lambda asset_id: self._long_asset(media, asset_id=asset_id),
            now=now,
        )

        assert plan is not None
        assert plan.segments[0].inpoint_seconds is None
        assert plan.segments[0].duration_seconds == 1800

    def test_following_items_are_not_offset(self, tmp_path: Path) -> None:
        media = tmp_path / "council.ts"
        media.write_text("fake", encoding="utf-8")
        now = datetime(2026, 6, 5, 18, 10, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[
                _schedule_item(scheduled_at=now - timedelta(minutes=10)),
                _schedule_item(
                    asset_id="next-meeting",
                    scheduled_at=now + timedelta(minutes=20),
                    duration_seconds=1200,
                ),
            ],
            asset_resolver=lambda asset_id: self._long_asset(media, asset_id=asset_id),
            now=now,
        )

        assert plan is not None
        assert len(plan.segments) == 2
        assert plan.segments[0].inpoint_seconds == 600
        assert plan.segments[1].inpoint_seconds is None
        # D42: the second item's SLOT is 1200s even though its media runs 1800s.
        # The slot is the contract -- the media is clipped to it, not the other
        # way round (this asserted 1800 before the fix, i.e. the item overran
        # its published slot by ten minutes).
        assert plan.segments[1].duration_seconds == 1200


def test_build_source_plan_from_schedule_returns_none_without_current_item(
    tmp_path: Path,
) -> None:
    media = tmp_path / "future.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[_schedule_item(scheduled_at=now + timedelta(minutes=15))],
        asset_resolver=lambda _asset_id: _asset(media),
        now=now,
    )

    assert plan is None


def test_build_source_plan_from_schedule_raises_for_missing_local_media(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 5, 18, 10, tzinfo=UTC)
    missing_media = tmp_path / "missing.ts"

    with pytest.raises(SourcePrepareError, match="local media file is missing"):
        build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now - timedelta(minutes=10))],
            asset_resolver=lambda _asset_id: _asset(missing_media),
            now=now,
        )


def test_build_source_plan_from_schedule_raises_for_invalid_trim_window(
    tmp_path: Path,
) -> None:
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 10, tzinfo=UTC)
    asset = _asset(media).model_copy(update={"trim_in_seconds": 120, "trim_out_seconds": 10})

    with pytest.raises(SourcePrepareError, match="invalid trim window"):
        build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now - timedelta(minutes=10))],
            asset_resolver=lambda _asset_id: asset,
            now=now,
        )


def test_schedule_source_plan_provider_calls_schedule_and_asset_resolvers(
    tmp_path: Path,
) -> None:
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    now = datetime(2026, 6, 5, 18, 10, tzinfo=UTC)
    seen: dict[str, str] = {}
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda channel_id: (
            seen.setdefault("channel_id", channel_id) and [_schedule_item(scheduled_at=now)]
        ),
        asset_resolver=lambda asset_id: seen.setdefault("asset_id", asset_id) and _asset(media),
        now_provider=lambda: now,
    )

    plan = provider("gov")

    assert plan is not None
    assert seen == {"channel_id": "gov", "asset_id": "council-meeting"}


def test_one_item_provider_resolves_each_future_boundary(tmp_path: Path) -> None:
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 23, 20, tzinfo=UTC)
    boundaries = (5, 30, 35)
    items = [
        _schedule_item(
            asset_id=f"program-{minute}",
            scheduled_at=start + timedelta(minutes=minute),
            duration_seconds=300,
        ).model_copy(update={"asset_title": f"Program :{20 + minute:02d}"})
        for minute in (0, *boundaries)
    ]
    assets = {
        item.asset_id: _asset(media, asset_id=item.asset_id).model_copy(
            update={
                "title": item.asset_title,
                "duration_seconds": 300,
                "trim_in_seconds": 0,
                "trim_out_seconds": 300,
            }
        )
        for item in items
    }
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        now_provider=lambda: start,
        max_segments=1,
    )

    assert provider("gov").segments[0].label == "Program :20"  # type: ignore[union-attr]
    assert [
        provider.plan_at("gov", start + timedelta(minutes=minute)).segments[0].label  # type: ignore[union-attr]
        for minute in boundaries
    ] == ["Program :25", "Program :50", "Program :55"]


def test_one_item_provider_restart_now_keeps_join_in_progress_trim(tmp_path: Path) -> None:
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 23, 20, tzinfo=UTC)
    item = _schedule_item(scheduled_at=start, duration_seconds=300)
    asset = _asset(media).model_copy(
        update={"duration_seconds": 300, "trim_in_seconds": 0, "trim_out_seconds": 300}
    )
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: [item],
        asset_resolver=lambda _asset_id: asset,
        now_provider=lambda: start + timedelta(seconds=75),
        max_segments=1,
    )

    plan = provider("gov")

    assert plan is not None
    assert len(plan.segments) == 1
    assert plan.segments[0].inpoint_seconds == 75
    assert plan.segments[0].duration_seconds == 225


def test_one_item_provider_future_gap_returns_none(tmp_path: Path) -> None:
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 23, 20, tzinfo=UTC)
    item = _schedule_item(scheduled_at=start, duration_seconds=300)
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: [item],
        asset_resolver=lambda _asset_id: _asset(media),
        now_provider=lambda: start,
        max_segments=1,
    )

    assert provider.plan_at("gov", start + timedelta(minutes=5, seconds=1)) is None


def test_looping_provider_repeats_the_published_sequence_after_the_last_item(
    tmp_path: Path,
) -> None:
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 23, 20, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="first", scheduled_at=start, duration_seconds=30),
        _schedule_item(
            asset_id="second", scheduled_at=start + timedelta(seconds=30), duration_seconds=30
        ),
    ]
    assets = {
        item.asset_id: _asset(media, asset_id=item.asset_id).model_copy(
            update={
                "title": item.asset_id,
                "duration_seconds": 30,
                "trim_in_seconds": 0,
                "trim_out_seconds": 30,
            }
        )
        for item in items
    }
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        max_segments=1,
        loop_schedule=True,
    )

    assert provider.plan_at("gov", start + timedelta(seconds=30)).segments[0].label == "second"  # type: ignore[union-attr]
    repeated = provider.plan_at("gov", start + timedelta(seconds=60))
    assert repeated is not None
    assert repeated.segments[0].label == "first"
    assert repeated.segments[0].schedule_occurrence_id == str(items[0].id)
    assert repeated.segments[0].schedule_occurrence_start == start + timedelta(seconds=60)


def test_looping_provider_preserves_gaps_between_published_items(tmp_path: Path) -> None:
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 23, 20, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="first", scheduled_at=start, duration_seconds=30),
        _schedule_item(
            asset_id="second", scheduled_at=start + timedelta(seconds=60), duration_seconds=30
        ),
    ]
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=lambda asset_id: _asset(media, asset_id=asset_id),
        max_segments=1,
        loop_schedule=True,
    )

    assert provider.plan_at("gov", start + timedelta(seconds=45)) is None


def test_schedule_window_projects_latest_repeat_run_with_original_ids_and_small_gaps():
    start = datetime(2026, 6, 1, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="old", scheduled_at=start - timedelta(days=1), duration_seconds=30),
        _schedule_item(asset_id="first", scheduled_at=start, duration_seconds=30),
        _schedule_item(
            asset_id="second", scheduled_at=start + timedelta(seconds=31), duration_seconds=30
        ),
        _schedule_item(asset_id="draft", scheduled_at=start + timedelta(days=1), state="scheduled"),
    ]
    original_times = [item.scheduled_at for item in items]
    projected = schedule_items_in_window(
        channel_id="gov",
        schedule_items=items,
        starts_at=start + timedelta(seconds=122),
        ends_at=start + timedelta(seconds=214),
        loop_schedule=True,
    )
    assert [(item.asset_id, (item.scheduled_at - start).total_seconds()) for item in projected] == [
        ("first", 122),
        ("second", 153),
        ("first", 183),
        ("second", 214),
    ]
    assert [item.id for item in projected] == [items[1].id, items[2].id] * 2
    assert [item.scheduled_at for item in items] == original_times
    assert (projected[1].scheduled_at - projected[0].scheduled_at).total_seconds() == 31


def test_schedule_window_in_gap_waits_for_next_item_and_nonrepeat_does_not_project():
    start = datetime(2026, 6, 1, tzinfo=UTC)
    items = [
        _schedule_item(scheduled_at=start, duration_seconds=30),
        _schedule_item(scheduled_at=start + timedelta(seconds=31), duration_seconds=30),
    ]
    assert [
        item.scheduled_at
        for item in schedule_items_in_window(
            channel_id="gov",
            schedule_items=items,
            starts_at=start + timedelta(seconds=30),
            ends_at=start + timedelta(seconds=40),
            loop_schedule=True,
        )
    ] == [start + timedelta(seconds=31)]
    assert (
        schedule_items_in_window(
            channel_id="gov",
            schedule_items=items,
            starts_at=start + timedelta(hours=1),
            ends_at=start + timedelta(hours=2),
            loop_schedule=False,
        )
        == []
    )
    with pytest.raises(ValueError, match="fourteen days"):
        schedule_items_in_window(
            channel_id="gov",
            schedule_items=items,
            starts_at=start,
            ends_at=start + timedelta(days=15),
        )


def test_looping_provider_covers_now_when_history_has_multiple_passes(tmp_path: Path) -> None:
    """RED (2026-09-21): the cycle boundary must be ONE contiguous playable pass.

    A real station's published schedule accumulates in separate contiguous
    passes (a pass, a multi-day gap, another pass). ``_repeat_schedule_cycle``
    currently derives its period from ``items[0]`` through ``max(end)`` over the
    WHOLE history, so the repeated "cycle" includes the multi-day inter-pass
    gaps. A ``current_time`` inside the repeated window then lands in one of
    those gaps, ``_current_item_index`` returns None, the plan is None, and the
    channel falls to slate instead of airing.

    This test builds two contiguous passes separated by a large gap and asks for
    a plan at a time that is inside the SECOND pass's repetition. It must
    produce a segment (an item covers that instant), not None.
    """
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")

    # Pass 1: 10:00-10:02. Gap: ~3 days. Pass 2: two 60 s items at 00:00 and 00:01.
    pass_one_start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    pass_two_start = datetime(2026, 6, 4, 0, 0, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="p1-a", scheduled_at=pass_one_start, duration_seconds=60),
        _schedule_item(
            asset_id="p1-b",
            scheduled_at=pass_one_start + timedelta(seconds=60),
            duration_seconds=60,
        ),
        _schedule_item(asset_id="p2-a", scheduled_at=pass_two_start, duration_seconds=60),
        _schedule_item(
            asset_id="p2-b",
            scheduled_at=pass_two_start + timedelta(seconds=60),
            duration_seconds=60,
        ),
    ]
    assets = {
        item.asset_id: _asset(media, asset_id=item.asset_id).model_copy(
            update={
                "title": item.asset_id,
                "duration_seconds": 60,
                "trim_in_seconds": 0,
                "trim_out_seconds": 60,
            }
        )
        for item in items
    }
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        max_segments=1,
        loop_schedule=True,
    )

    # Pass 2 spans 00:00-00:02 on 2026-06-04; its period is 120 s, so 00:00:30
    # on BOTH the next and following cycles must still air p2-a, not go to slate.
    for offset_days in (0, 1, 2):
        probe = pass_two_start + timedelta(days=offset_days, seconds=30)
        plan = provider.plan_at("gov", probe)
        assert plan is not None, f"no plan at {probe}: the loop's cycle includes the inter-pass gap"
        assert plan.segments[0].label == "p2-a"


def test_resolver_module_exports_source_plan_contracts() -> None:
    assert resolver.ScheduleSourcePlanProvider is ScheduleSourcePlanProvider
    assert resolver.SlateSourceGenerator is SlateSourceGenerator
    assert resolver.build_source_plan_from_schedule is build_source_plan_from_schedule
    assert resolver.build_slate_source_args is build_slate_source_args


def test_slate_plan_is_one_continuous_fill_file_covering_the_horizon(tmp_path: Path) -> None:
    # U27: a plan of N repeats of one finite file made the bridge build N
    # decoder sub-chains, and the hand-off between them desynced audio from
    # video on the live caption shape. One stream-copied fill file keeps a
    # single decoder chain (nothing to hand off) while still spanning the
    # whole fill horizon, so the encoder is not relaunched inside it (CA-8).
    calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(args)
        Path(args[-1]).write_bytes(b"ts")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    generator = SlateSourceGenerator(
        work_dir=tmp_path, ffmpeg_runner=runner, target_fill_seconds=3600
    )

    plan = generator(_config())

    assert len(plan.segments) == 1
    segment = plan.segments[0]
    assert segment.kind == "slate"
    assert Path(segment.path).name.startswith("slate-fill-")
    assert segment.duration_seconds == 3600
    # one slate render + one concat stream copy of it
    assert len(calls) == 2
    assert calls[1][:3] == ["-f", "concat", "-safe"]
    assert Path(calls[1][-1]).name.startswith("."), "assembled via a staging file"


def test_slate_fill_is_reused_until_the_rendered_slate_changes(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(args)
        Path(args[-1]).write_bytes(b"ts")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    generator = SlateSourceGenerator(
        work_dir=tmp_path, ffmpeg_runner=runner, target_fill_seconds=600
    )

    first = generator(_config())
    again = generator(_config())
    changed = generator(
        _config().model_copy(update={"slate_message": "Scheduled programming resumes shortly."})
    )
    rebuilds = [args for args in calls if args[:2] == ["-f", "concat"]]

    assert first.segments[0].path == again.segments[0].path
    assert changed.segments[0].path != first.segments[0].path
    assert len(rebuilds) == 2, "one fill per rendered slate, reused in between"
    assert Path(first.segments[0].path).exists()
    assert Path(changed.segments[0].path).exists()
    assert Path(again.segments[0].path).stat().st_size > 0


def test_slate_fill_failure_raises_instead_of_falling_back_to_repeats(tmp_path: Path) -> None:
    # The multi-segment plan IS the defect, so a failed concat must surface as
    # SourcePrepareError: falling back to repeats would put the desync back on
    # air, and a 1x30s plan would relaunch the encoder every 30s (CA-8).
    calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(args)
        if args[:2] == ["-f", "concat"]:
            return FfmpegResult(returncode=1, stdout="", stderr="boom")
        Path(args[-1]).write_bytes(b"ts")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    generator = SlateSourceGenerator(
        work_dir=tmp_path, ffmpeg_runner=runner, target_fill_seconds=3600
    )

    with pytest.raises(SourcePrepareError, match="slate fill"):
        generator(_config())
    assert len(calls) == 2


def test_slate_cache_is_immutable_across_changed_content(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(args)
        Path(args[-1]).write_bytes(b"ts")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    generator = SlateSourceGenerator(work_dir=tmp_path, ffmpeg_runner=runner)
    first = generator(_config())
    again = generator(_config())
    changed = generator(
        _config().model_copy(update={"slate_message": "Scheduled programming resumes shortly."})
    )

    renders = [args for args in calls if "-vf" in args or "lavfi" in args]
    assert len(renders) == 2, "one render per distinct slate message"
    assert first.segments[0].path == again.segments[0].path
    assert changed.segments[0].path != first.segments[0].path
    assert Path(first.segments[0].path).exists()
    assert Path(changed.segments[0].path).exists()


def _asset_of(
    path: Path,
    *,
    duration_seconds: int,
    asset_id: str = "council-meeting",
    trim_in: float | None = None,
    trim_out: float | None = None,
) -> StaffAssetRow:
    return StaffAssetRow(
        asset_id=asset_id,
        title="Council Meeting",
        state="validated",
        file_path=str(path),
        duration_seconds=duration_seconds,
        trim_in_seconds=trim_in,
        trim_out_seconds=trim_out,
    )


def _media(tmp_path: Path) -> Path:
    media = tmp_path / "council.ts"
    media.write_text("fake", encoding="utf-8")
    return media


def _slot_schedule(
    now: datetime, *, count: int, slot_seconds: int = 30
) -> list[ScheduleItemResponse]:
    return [
        _schedule_item(
            asset_id=f"item-{index}",
            scheduled_at=now + timedelta(seconds=slot_seconds * index),
            duration_seconds=slot_seconds,
        )
        for index in range(count)
    ]


class TestSlotDuration:
    """D42 (real-hardware soak on the tester, 2026-09-05).

    ``_segment_duration`` returned the ASSET's playable length and ignored
    ``item.duration_seconds`` (the published schedule slot) entirely: a 30s
    slot holding an hour of media aired for the whole hour, so the schedule
    was not honoured at all; conversely a schedule of short assets built a
    plan far shorter than the slots it covered. The slot is the contract; the
    media can only ever cut it short.
    """

    def test_long_media_is_clipped_to_its_thirty_second_slot(self, tmp_path: Path) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=4),
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id
            ),
            now=now,
        )

        assert plan is not None
        # Before the fix every one of these was 3600s: an hour of media in a
        # 30-second slot, playing straight over the next three programs.
        assert [segment.duration_seconds for segment in plan.segments] == [30.0, 30.0, 30.0, 30.0]

    def test_join_in_progress_offsets_within_the_slot_not_the_asset(self, tmp_path: Path) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, 20, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[
                _schedule_item(scheduled_at=now - timedelta(seconds=20), duration_seconds=30)
            ],
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id
            ),
            now=now,
        )

        assert plan is not None
        segment = plan.segments[0]
        assert segment.inpoint_seconds == 20  # 20s into the slot
        assert segment.duration_seconds == 10  # the 10s of slot that remain
        assert segment.playable_duration_seconds == 3600

    def test_join_in_progress_preserves_full_trimmed_clip_duration(self, tmp_path: Path) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, 3, 100000, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[
                _schedule_item(scheduled_at=now - timedelta(seconds=3.1), duration_seconds=1800)
            ],
            asset_resolver=lambda _asset_id: _asset_of(
                media,
                duration_seconds=3600,
                trim_in=5,
                trim_out=25,
            ),
            now=now,
        )

        assert plan is not None
        segment = plan.segments[0]
        assert segment.duration_seconds == pytest.approx(16.9)
        assert segment.inpoint_seconds == pytest.approx(8.1)
        assert segment.outpoint_seconds == 25
        assert segment.playable_duration_seconds == 20

    def test_a_trim_window_longer_than_the_slot_clips_and_moves_the_outpoint(
        self, tmp_path: Path
    ) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=now, duration_seconds=30)],
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id, trim_in=100, trim_out=900
            ),
            now=now,
        )

        assert plan is not None
        segment = plan.segments[0]
        assert segment.duration_seconds == 30
        assert segment.inpoint_seconds == 100
        # A stale 900 out-point would let a trim-aware consumer
        # (preparer._emit_prepared_from_cache with playout_trim_supported)
        # emit 800s of media for a 30s slot.
        assert segment.outpoint_seconds == 130

    def test_media_shorter_than_its_slot_ends_the_plan_for_the_fill_policy(
        self, tmp_path: Path
    ) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        def resolver(asset_id: str) -> StaffAssetRow:
            # The FIRST item's media runs 400s inside a 600s slot; the rest
            # fill their slots exactly.
            duration = 400 if asset_id == "item-0" else 600
            return _asset_of(media, duration_seconds=duration, asset_id=asset_id)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=4, slot_seconds=600),
            asset_resolver=resolver,
            now=now,
        )

        assert plan is not None
        # The 200s the media cannot cover belong to the channel's fill policy
        # (bulletin_filler.FillerSourceProvider -> bulletins or slate), reached
        # through the daemon's FALLBACK_SLATE gap-replan -- the same honest
        # answer the already-aired current item gives. Nothing loops the media,
        # and item-1 is NOT started 200 seconds early.
        assert [segment.duration_seconds for segment in plan.segments] == [400.0]

    def test_media_within_the_gap_tolerance_of_its_slot_still_continues(
        self, tmp_path: Path
    ) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=3),
            # 29.97s of media in a 30s slot must not truncate the plan on
            # every single build.
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=30, asset_id=asset_id, trim_in=0.0, trim_out=29.97
            ),
            now=now,
        )

        assert plan is not None
        assert len(plan.segments) == 3


class TestPlanWindow:
    """D43 (superseded by D45): the plan window used to be bounded by
    DURATION as well as by count -- ``PLAN_MIN_SECONDS`` defaulted to 1800.0
    so a schedule of 30-second slots chased a 60-segment, 1800-second plan
    instead of the count-only 8-segment, 240-second one.

    Real-hardware soak evidence (3 GStreamer channels, 30-second items
    back-to-back) measured what that duration target actually cost:
    ``bridge.graph_from_config`` builds ONE decoder sub-chain PER segment in
    a single pipeline set to PLAYING all at once, so 60 segments produced
    ~1200 avdec_h264 threads and ~3.5 GB on one worker -- no TS output landed
    inside the engine's 10s stall watchdog, and every worker relaunched
    roughly every 30s. D45 reverts ``PLAN_MIN_SECONDS`` to 0.0: a normal
    plan's segment count is bounded by ``max_segments`` (pipeline shape)
    alone by default now. A caller that explicitly wants a longer
    duration-bounded window (and can bear the bigger pipeline) can still opt
    in via ``min_plan_seconds`` -- exercised below too.
    """

    def test_thirty_second_slots_build_only_max_segments_by_default(self, tmp_path: Path) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=200),
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id
            ),
            now=now,
        )

        assert plan is not None
        # D45: PLAN_MIN_SECONDS defaults to 0.0 -- max_segments (8 by
        # default) is what bounds a normal plan's segment count, not a
        # duration target that used to build 60 segments (and ~1200 decoder
        # threads) out of 30-second slots.
        assert PLAN_MIN_SECONDS == 0.0
        assert len(plan.segments) == 8
        total = sum(segment.duration_seconds for segment in plan.segments)
        assert total == 240.0

    def test_min_plan_seconds_widens_the_window_only_up_to_the_pipeline_cap(
        self, tmp_path: Path
    ) -> None:
        """Hostile-review fix (2026-09-05): a caller cannot opt its way past
        ``MAX_PLAYLIST_SUBCHAINS`` -- the pipeline-shape ceiling always wins,
        because ``build_source_plan_from_schedule`` is the plan's only
        producer and every OTHER consumer (``automation.py``'s rollover-
        horizon tracking, ``daemon.py``, ``continuity.py``, ``preparer.py``)
        trusts whatever segment count it returns as the plan the pipeline
        will actually play. 1800s of planned duration out of 30-second slots
        would need 60 segments; the default ``segment_cap``
        (``MAX_PLAYLIST_SUBCHAINS``, 12) stops it at 360s instead."""
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=200),
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id
            ),
            now=now,
            # A caller that explicitly wants a longer duration-bounded plan
            # can still ask for it -- but not past the pipeline cap.
            min_plan_seconds=1800.0,
        )

        assert plan is not None
        assert len(plan.segments) == MAX_PLAYLIST_SUBCHAINS
        total = sum(segment.duration_seconds for segment in plan.segments)
        assert total == MAX_PLAYLIST_SUBCHAINS * 30.0
        assert total < 1800.0

    def test_an_explicit_segment_cap_above_the_pipeline_ceiling_is_clamped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hostile-review fix: a caller cannot ask its way past
        ``MAX_PLAYLIST_SUBCHAINS`` by passing an explicit larger
        ``segment_cap``/``max_segments`` either -- both are clamped down
        (with a WARNING naming the channel), because a plan larger than one
        pipeline can safely decode is exactly the regression this module
        exists to prevent. 1-second slots: 1800 of them would be needed to
        reach ``min_plan_seconds``, and an oversized ``segment_cap`` (120,
        the module's pre-D45 historical ceiling) would let the segment
        count get there if it were still honoured."""
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        with caplog.at_level("WARNING"):
            plan = build_source_plan_from_schedule(
                channel_id="gov",
                schedule_items=_slot_schedule(now, count=400, slot_seconds=1),
                asset_resolver=lambda asset_id: _asset_of(
                    media, duration_seconds=3600, asset_id=asset_id
                ),
                now=now,
                min_plan_seconds=1800.0,
                segment_cap=120,
            )

        assert plan is not None
        assert len(plan.segments) == MAX_PLAYLIST_SUBCHAINS
        assert any(
            "gov" in record.message and "MAX_PLAYLIST_SUBCHAINS" in record.message
            for record in caplog.records
        )

    def test_long_items_still_stop_at_max_segments(self, tmp_path: Path) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            # 30-minute slots: the count bound (max_segments) is what
            # applies regardless of min_plan_seconds. A long-item schedule
            # is unaffected by D45.
            schedule_items=_slot_schedule(now, count=40, slot_seconds=1800),
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=1800, asset_id=asset_id
            ),
            now=now,
        )

        assert plan is not None
        assert len(plan.segments) == 8

    def test_end_to_end_a_thirty_second_schedule_never_exceeds_eight_subchains(
        self, tmp_path: Path
    ) -> None:
        """Hostile-review fix (2026-09-05), BLOCKER 1: the clamp has to live
        at the plan's producer, not just in ``bridge.graph_from_config`` --
        otherwise a consumer that trusts the plan's own segment count
        (``automation.py``'s rollover-horizon tracking, ``daemon.py``'s
        dispatched-plan bookkeeping) disagrees with what the pipeline
        actually plays. Proven end-to-end here: build a real plan from a
        30-second-item schedule with ``build_source_plan_from_schedule``,
        feed that SAME plan into ``graph_from_config``, and check the two
        never disagree about the segment/sub-chain count -- not just that
        each is separately capped."""
        from civiccast.egress.gst.bridge import graph_from_config
        from civiccast.egress.gst.graph import PlaylistLeg

        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=200),
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id
            ),
            now=now,
        )
        assert plan is not None
        assert len(plan.segments) <= 8

        graph = graph_from_config(_config(), plan)
        program, _slate = graph.sources
        assert isinstance(program, PlaylistLeg)
        assert len(program.subchains) <= 8
        # The invariant BLOCKER 1 asked for: the plan built by the producer
        # and the graph built by the consumer agree on the segment count --
        # nothing was silently truncated on the bridge side because the
        # producer had already handed it a plan the pipeline can play in
        # full.
        assert len(program.subchains) == len(plan.segments)

    def test_end_to_end_agreement_holds_exactly_AT_the_cap(self, tmp_path: Path) -> None:
        """Item 6: the test above proves agreement well UNDER the cap
        (``max_segments=8``); prove it also holds exactly AT the cap, where
        a prior version of ``graph_from_config`` would have hit its own
        (now removed) truncation branch instead of building the full plan.
        A caller that explicitly asks for ``max_segments=MAX_PLAYLIST_
        SUBCHAINS`` gets a plan of exactly that many segments, and
        ``graph_from_config`` builds exactly that many sub-chains from it --
        not one fewer."""
        from civiccast.egress.gst.bridge import graph_from_config
        from civiccast.egress.gst.graph import PlaylistLeg

        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)

        plan = build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=_slot_schedule(now, count=200),
            asset_resolver=lambda asset_id: _asset_of(
                media, duration_seconds=3600, asset_id=asset_id
            ),
            now=now,
            max_segments=MAX_PLAYLIST_SUBCHAINS,
        )
        assert plan is not None
        assert len(plan.segments) == MAX_PLAYLIST_SUBCHAINS

        graph = graph_from_config(_config(), plan)
        program, _slate = graph.sources
        assert isinstance(program, PlaylistLeg)
        assert len(program.subchains) == MAX_PLAYLIST_SUBCHAINS
        assert len(program.subchains) == len(plan.segments)

    def test_min_plan_seconds_and_segment_cap_are_validated(self, tmp_path: Path) -> None:
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)
        resolver = lambda asset_id: _asset_of(  # noqa: E731
            media, duration_seconds=3600, asset_id=asset_id
        )

        with pytest.raises(ValueError, match="segment_cap"):
            build_source_plan_from_schedule(
                channel_id="gov",
                schedule_items=_slot_schedule(now, count=2),
                asset_resolver=resolver,
                now=now,
                max_segments=8,
                segment_cap=4,
            )
        with pytest.raises(ValueError, match="min_plan_seconds"):
            build_source_plan_from_schedule(
                channel_id="gov",
                schedule_items=_slot_schedule(now, count=2),
                asset_resolver=resolver,
                now=now,
                min_plan_seconds=-1.0,
            )

    def test_an_inconsistent_pair_above_the_cap_raises_rather_than_clamping_silently(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hostile-review fix (2026-09-05): the raw-pair validation
        (``segment_cap`` must be at least ``max_segments``) has to run
        BEFORE the ``MAX_PLAYLIST_SUBCHAINS`` clamp, not after -- otherwise
        an inconsistent caller-supplied pair that both happen to exceed the
        pipeline cap (``max_segments=20``, ``segment_cap=15``) would get
        silently clamped down to an agreeing pair (12/12) instead of
        surfacing that the caller's own request never made sense on its own
        terms. A CONSISTENT pair above the cap (20/20) is a different
        case -- both values agree with each other, so they are clamped down
        together (with a WARNING), not rejected."""
        media = _media(tmp_path)
        now = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)
        resolver = lambda asset_id: _asset_of(  # noqa: E731
            media, duration_seconds=3600, asset_id=asset_id
        )

        with pytest.raises(ValueError, match="segment_cap"):
            build_source_plan_from_schedule(
                channel_id="gov",
                schedule_items=_slot_schedule(now, count=2),
                asset_resolver=resolver,
                now=now,
                max_segments=20,
                segment_cap=15,
            )

        with caplog.at_level("WARNING"):
            plan = build_source_plan_from_schedule(
                channel_id="gov",
                schedule_items=_slot_schedule(now, count=200),
                asset_resolver=resolver,
                now=now,
                max_segments=20,
                segment_cap=20,
            )
        assert plan is not None
        assert len(plan.segments) == MAX_PLAYLIST_SUBCHAINS
        assert any(
            "gov" in record.message and "MAX_PLAYLIST_SUBCHAINS" in record.message
            for record in caplog.records
        )


def test_a_boundary_past_a_closing_slot_resolves_the_next_scheduled_item(
    tmp_path: Path,
) -> None:
    """U26 evidence for the daemon's degenerate-tail guard.

    The guard resolves the schedule at "now + the tail's own remaining seconds +
    a margin" (daemon._SCHEDULE_TAIL_FLOOR_SECONDS /
    _SCHEDULE_TAIL_BOUNDARY_MARGIN_S). For that to land the channel on the
    program it was asked for, the item whose slot is closing must be gone at
    that instant and the NEXT published item must be the one that resolves --
    proven here against the real provider: a 300s slot with 9.2s left (the
    fixture's stand-in for the live log's ~11s remainder), boundary taken 10.2s
    past the tail's start.
    """
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 6, 5, 18, 0, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="weather", scheduled_at=start, duration_seconds=300).model_copy(
            update={"asset_title": "Longmont Weather :16"}
        ),
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=300),
            duration_seconds=600,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    assets = {
        item.asset_id: _asset(media, asset_id=item.asset_id).model_copy(
            update={
                "title": item.asset_title,
                "duration_seconds": 1800,
                "trim_in_seconds": None,
                "trim_out_seconds": None,
            }
        )
        for item in items
    }
    tail_now = start + timedelta(seconds=290.8)
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        now_provider=lambda: tail_now,
        # The production wiring with the GStreamer engine selected
        # (automation.py:2634), which is what makes a closing slot resolve to a
        # bare tail in the first place.
        max_segments=1,
    )

    tail = provider("gov")

    assert tail is not None
    assert len(tail.segments) == 1
    assert tail.segments[0].label == "Longmont Weather :16"
    assert tail.segments[0].duration_seconds == pytest.approx(9.2)

    boundary = provider.plan_at("gov", tail_now + timedelta(seconds=9.2 + 1.0))

    assert boundary is not None
    assert boundary.segments[0].label == "City Council"
    # One second into the item's own slot, so one second into its media.
    assert boundary.segments[0].inpoint_seconds == pytest.approx(1.0)


# --------------------------------------------------------------------------
# U26 gap absorb (reports/U26.md item 3, Option D). The case it covers is a
# HOLE in the schedule: an instant that falls after one item's window closed and
# before the next one's opened resolves to no plan at all. Automation then fills
# that hole with filler, the channel leaves its program for a slate epoch, and
# the switch back goes through FALLBACK_SLATE's immediate-reload path (daemon
# F3(b): worker exit, relay replacement). gap_absorb_seconds resolves the
# boundary straight to the item due within the window, so the deferred switch is
# program -> program, in-worker, with no exit.
#
# The two archived live incidents (government, 2026-09-25 02:13 / 02:58 MDT)
# were NOT that case: their closing item's own plan end was still ~9.6s / ~11.0s
# away when the engine reached EOS, and the boundary provider answered with that
# still-open item -- neither log contains a `target=filler` rollover. What
# repaired them is the daemon's tail floor, not this constant. See
# reports/U26.md items 1-2.
# --------------------------------------------------------------------------


def _gap_absorb_provider(
    tmp_path: Path,
    items: list[ScheduleItemResponse],
    media_seconds: dict[str, float],
    *,
    gap_absorb_seconds: float | None = SCHEDULE_GAP_ABSORB_SECONDS,
) -> ScheduleSourcePlanProvider:
    """Build the production-shaped provider for a gap-absorb case.

    ``media_seconds`` is the playable media length per asset id; the item's own
    ``duration_seconds`` is its SLOT. A media length shorter than the slot is
    the live shape: the station's engine reaches EOS while the slot still has
    time left on it.
    """

    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    assets = {
        item.asset_id: _asset(media, asset_id=item.asset_id).model_copy(
            update={
                "title": item.asset_title,
                "duration_seconds": media_seconds[item.asset_id],
                "trim_in_seconds": None,
                "trim_out_seconds": media_seconds[item.asset_id],
            }
        )
        for item in items
    }
    kwargs: dict[str, float] = {}
    if gap_absorb_seconds is not None:
        kwargs["gap_absorb_seconds"] = gap_absorb_seconds
    return ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        # The production wiring with the GStreamer engine selected
        # (automation.py's build), which is what makes a closing slot resolve
        # to a bare tail / to nothing at all.
        max_segments=1,
        **kwargs,
    )


def test_a_boundary_at_a_closing_slot_whose_media_is_gone_resolves_the_next_item(
    tmp_path: Path,
) -> None:
    """The live 02:58:19 shape: media gone, slot still open, next item 8s out.

    The ending item's segment duration IS the plan horizon automation records
    (min(slot, media) = the media length), so the boundary lands exactly where
    its media runs out -- with the slot still open and the next item due inside
    the absorb window. Before the fix that boundary resolved to None (filler);
    the live consequence was the slate epoch and the F3(b) exit.
    """

    start = datetime(2026, 9, 25, 2, 58, tzinfo=UTC)
    items = [
        _schedule_item(
            asset_id="weather",
            scheduled_at=start - timedelta(seconds=300),
            duration_seconds=300,
        ).model_copy(update={"asset_title": "Longmont Weather :16"}),
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=4),
            duration_seconds=1800,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    provider = _gap_absorb_provider(tmp_path, items, {"weather": 296.0, "council": 1800.0})

    plan = provider.plan_at("gov", start - timedelta(seconds=4))

    assert plan is not None
    assert [segment.label for segment in plan.segments] == ["City Council"]
    # The next item's own media, taken whole from its beginning: the caller
    # starts it 4s early (the gap), it does not skip 4s into it.
    assert plan.segments[0].duration_seconds == pytest.approx(1800.0)
    assert plan.segments[0].inpoint_seconds is None


@pytest.mark.parametrize("previous_end_seconds_ago", [0, 3600])
def test_a_boundary_inside_the_gap_resolves_the_next_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_end_seconds_ago: int
) -> None:
    """The bare-gap form: the instant is between two items, nothing covers it."""

    start = datetime(2026, 9, 25, 3, 35, tzinfo=UTC)
    items = [
        _schedule_item(
            asset_id="weather",
            scheduled_at=start - timedelta(seconds=300 + previous_end_seconds_ago),
            duration_seconds=300,
        ).model_copy(update={"asset_title": "Longmont Weather :16"}),
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=6),
            duration_seconds=1800,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    provider = _gap_absorb_provider(tmp_path, items, {"weather": 300.0, "council": 1800.0})
    monkeypatch.setattr(provider, "_now_provider", lambda: start)

    # Ordinary slate polling must wait, even with an old published item in
    # the log. Gap absorption belongs to an explicit programme boundary.
    assert provider("gov") is None

    plan = provider.plan_at("gov", start)

    assert plan is not None
    assert [segment.label for segment in plan.segments] == ["City Council"]
    monkeypatch.setattr(provider, "_now_provider", lambda: start + timedelta(seconds=6))
    due_plan = provider("gov")
    assert due_plan is not None
    assert [segment.label for segment in due_plan.segments] == ["City Council"]
    assert due_plan.segments[0].inpoint_seconds is None


def test_a_gap_beyond_the_absorb_window_still_falls_back(tmp_path: Path) -> None:
    """A real gap keeps the old behavior: no plan, so automation fills it."""

    start = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="weather", scheduled_at=start, duration_seconds=300).model_copy(
            update={"asset_title": "Longmont Weather :16"}
        ),
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=300 + 60),
            duration_seconds=1800,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    provider = _gap_absorb_provider(tmp_path, items, {"weather": 300.0, "council": 1800.0})

    # One second past the window: 60s of nothing to air is a real gap.
    assert provider.plan_at("gov", start + timedelta(seconds=301)) is None
    assert provider.plan_at("gov", start + timedelta(seconds=300 + 60 - 31)) is None


def test_a_healthy_remainder_is_never_cut_short_for_the_next_item(tmp_path: Path) -> None:
    """Content is never replaced: a live item's remaining media is not a stub.

    The next item here IS inside the absorb window (14s out), but the item
    covering the instant still has 10s of its own media left to air. A
    join-in-progress resume of live content is what the plan is for, so the
    absorb must leave it alone -- otherwise a mid-program (re)start would skip
    the end of the program it was airing.
    """

    start = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)
    items = [
        _schedule_item(asset_id="weather", scheduled_at=start, duration_seconds=300).model_copy(
            update={"asset_title": "Longmont Weather :16"}
        ),
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=304),
            duration_seconds=1800,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    provider = _gap_absorb_provider(tmp_path, items, {"weather": 300.0, "council": 1800.0})

    plan = provider.plan_at("gov", start + timedelta(seconds=290))

    assert plan is not None
    assert [segment.label for segment in plan.segments] == ["Longmont Weather :16"]
    assert plan.segments[0].duration_seconds == pytest.approx(10.0)


def test_the_gap_absorb_is_off_unless_the_caller_asks_for_it(tmp_path: Path) -> None:
    """Default 0.0: a direct caller keeps the documented no-early-start answer."""

    start = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
    items = [
        _schedule_item(
            asset_id="weather",
            scheduled_at=start - timedelta(seconds=300),
            duration_seconds=300,
        ).model_copy(update={"asset_title": "Longmont Weather :16"}),
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=6),
            duration_seconds=1800,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    provider = _gap_absorb_provider(
        tmp_path, items, {"weather": 300.0, "council": 1800.0}, gap_absorb_seconds=None
    )

    assert provider.plan_at("gov", start) is None
    assert (
        build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=items,
            asset_resolver=lambda asset_id: _asset(tmp_path / "program.ts", asset_id=asset_id),
            now=start,
        )
        is None
    )


def test_a_plan_before_the_published_log_still_waits_for_its_first_item(
    tmp_path: Path,
) -> None:
    """No early start before the log begins: there is no ending item to be at.

    A station that comes up early is not in a gap BETWEEN two items -- it is
    simply early, and the first item's published start time is the contract.
    """

    start = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)
    items = [
        _schedule_item(
            asset_id="council",
            scheduled_at=start + timedelta(seconds=20),
            duration_seconds=1800,
        ).model_copy(update={"asset_title": "City Council"}),
    ]
    provider = _gap_absorb_provider(tmp_path, items, {"council": 1800.0})

    assert provider.plan_at("gov", start) is None
    # And once the log has started, a later gap still absorbs.
    assert provider.plan_at("gov", start + timedelta(seconds=20)) is not None


def test_gap_absorb_seconds_rejects_a_negative_window(tmp_path: Path) -> None:
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")
    start = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="gap_absorb_seconds"):
        build_source_plan_from_schedule(
            channel_id="gov",
            schedule_items=[_schedule_item(scheduled_at=start)],
            asset_resolver=lambda _asset_id: _asset(media),
            now=start,
            gap_absorb_seconds=-1.0,
        )

    with pytest.raises(ValueError, match="gap_absorb_seconds"):
        ScheduleSourcePlanProvider(
            schedule_items_provider=lambda _channel_id: [],
            asset_resolver=lambda _asset_id: None,
            gap_absorb_seconds=-1.0,
        )


# --------------------------------------------------------------------------
# U36 (2026-09-25): a program->program rollover whose RECORDED duration runs
# past the media's real end.
#
# Live incident (channel `public`, 14:06:50 - 14:18:02): the planner's plan end
# for the July 23 weather report was 3.6-6.5s past the media's real end
# (probe: video=667.100000 audio=667.114000 format=667.114000). On the
# station's engine every plan is ONE leg (`max_segments=1`), so the leg's true
# EOS ended the pipeline: the worker exited 0 while ON_AIR, and the relaunch
# re-resolved the same lying clock into a 2-second sliver of the program that
# had already ended (`the live plan ends in 2s`) -- then exited the same way
# again, tripping `restart #2; backing off`. See reports/U36.md section 1.
#
# The planner's durations come from the DATABASE rows (`item.duration_seconds`,
# `asset.duration_seconds`/the trim window) and are never checked against the
# media file. These tests pin the fix: a segment is capped at the media's REAL
# playable duration, so a plan never ends past it.
#
# The probe is injected here rather than measured: this module stays
# ffprobe-free and deterministic (the rest of its ~50 callers use a 4-byte
# `fake` file that ffprobe honestly answers None for -- which is also why the
# existing cases are unaffected). tests/egress/test_u36_rollover_sliver_real_ffmpeg.py
# is the same shape against real media.
# --------------------------------------------------------------------------


def _u36_provider(
    tmp_path: Path,
    items: list[ScheduleItemResponse],
    *,
    recorded_seconds: dict[str, float],
    gap_absorb_seconds: float | None = SCHEDULE_GAP_ABSORB_SECONDS,
) -> ScheduleSourcePlanProvider:
    """Production-shaped provider for a rollover past the media's real end.

    Each asset gets its OWN fake media file, so a path-keyed probe can tell
    them apart. ``recorded_seconds`` is what the database rows claim --
    ``asset.duration_seconds`` AND the trim window -- deliberately longer than
    the real media for the item under test, which is the live shape.
    """

    assets: dict[str, StaffAssetRow] = {}
    for item in items:
        media = tmp_path / f"{item.asset_id}.ts"
        media.write_text("fake", encoding="utf-8")
        assets[item.asset_id] = _asset(media, asset_id=item.asset_id).model_copy(
            update={
                "title": item.asset_title,
                "duration_seconds": recorded_seconds[item.asset_id],
                "trim_in_seconds": None,
                "trim_out_seconds": recorded_seconds[item.asset_id],
            }
        )
    kwargs: dict[str, float] = {}
    if gap_absorb_seconds is not None:
        kwargs["gap_absorb_seconds"] = gap_absorb_seconds
    return ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        # The production wiring with the GStreamer engine selected
        # (automation.py:2628): ONE leg per plan, so the leg's EOS is the
        # pipeline's EOS.
        max_segments=1,
        **kwargs,
    )


def _u36_truthful_probe(
    real_media_seconds: dict[str, float],
    calls: list[str] | None = None,
) -> object:
    """A stand-in for the real ffprobe-backed media probe, keyed by file name."""

    def probe(path: Path) -> float | None:
        name = Path(path).name
        if calls is not None:
            calls.append(name)
        return real_media_seconds.get(name)

    return probe


_U36_START = datetime(2026, 9, 25, 14, 6, 41, tzinfo=UTC)
# The measured live overshoot: the July 23 upload is 667.114s while its rows
# let the planner end the plan 3.6-6.5s later.
_U36_REAL_MEDIA = {"weather.ts": 174.5, "council.ts": 120.0}


def _u36_two_short_programs() -> list[ScheduleItemResponse]:
    return [
        _schedule_item(
            asset_id="weather",
            scheduled_at=_U36_START,
            duration_seconds=180,
        ).model_copy(update={"asset_title": "Weather Report"}),
        _schedule_item(
            asset_id="council",
            scheduled_at=_U36_START + timedelta(seconds=180),
            duration_seconds=120,
        ).model_copy(update={"asset_title": "City Council"}),
    ]


def test_u36_a_rollover_past_the_media_end_never_ends_its_plan_past_that_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live defect, at the plan level.

    `monkeypatch` (raising=False: this is the RED commit -- the module attribute
    the fix consults does not exist yet, and the test must fail on the PLAN, not
    on an AttributeError) installs a truthful media probe. The schedule's rows
    claim 180s / 120s; the media is really 174.5s / 120s. The boundary is 178s
    into the first program's 180s slot -- inside the recorded slot, 3.5s past
    the media's real end, which is exactly where the 14:17:52 relaunch landed.
    """

    monkeypatch.setattr(
        source_plan_module,
        "probe_media_duration_seconds",
        _u36_truthful_probe(_U36_REAL_MEDIA),
        raising=False,
    )
    provider = _u36_provider(
        tmp_path,
        _u36_two_short_programs(),
        recorded_seconds={"weather": 180.0, "council": 120.0},
    )

    plan = provider.plan_at("gov", _U36_START + timedelta(seconds=178))

    assert plan is not None
    # Clause 1: no segment may start at or past its own media's real end. The
    # live sliver was the July 23 program at inpoint 178.0 for 2.0s -- a leg
    # 3.5s past the end of media that had already played.
    for segment in plan.segments:
        start_seconds = segment.inpoint_seconds or 0.0
        assert (
            start_seconds + segment.duration_seconds
            <= _U36_REAL_MEDIA[Path(segment.path).name] + 0.05
        ), (
            f"segment {segment.label!r} airs to "
            f"{start_seconds + segment.duration_seconds}s of a "
            f"{_U36_REAL_MEDIA[Path(segment.path).name]}s file"
        )
    # Clause 2: with the exhausted program recognised as exhausted, the
    # already-wired gap-absorb hands the boundary to the NEXT program (due 2s
    # later) instead of re-airing the tail of the one that just ended.
    assert [segment.source_ref for segment in plan.segments] == ["council"]
    assert plan.segments[0].duration_seconds == pytest.approx(120.0)
    assert plan.segments[0].inpoint_seconds in (None, 0.0)


def test_u36_a_segment_is_capped_to_the_media_it_really_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Join-in-progress at the item's own start: the row's 180s is not the
    number the engine can play, so it is not the number the plan may use."""

    monkeypatch.setattr(
        source_plan_module,
        "probe_media_duration_seconds",
        _u36_truthful_probe(_U36_REAL_MEDIA),
        raising=False,
    )
    provider = _u36_provider(
        tmp_path,
        _u36_two_short_programs(),
        recorded_seconds={"weather": 180.0, "council": 120.0},
    )

    plan = provider.plan_at("gov", _U36_START)

    assert plan is not None
    assert plan.segments[0].source_ref == "weather"
    assert plan.segments[0].duration_seconds == pytest.approx(174.5)


@pytest.mark.parametrize(
    ("offset", "expected_duration", "occurrence_offset"),
    [(0, 37.987822, 0), (37, 0.987822, 0), (38, 37.987822, 38)],
)
def test_repeat_uses_precise_untrimmed_media_duration(
    tmp_path: Path, offset: int, expected_duration: float, occurrence_offset: int
) -> None:
    media = _media(tmp_path)
    item = _schedule_item(scheduled_at=_U36_START, duration_seconds=38)
    # Ingest stores int(float(raw_duration)); the media retains the fraction.
    asset = _asset_of(media, duration_seconds=37)
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _: [item],
        asset_resolver=lambda _: asset,
        media_duration_resolver=lambda _: 37.987822,
        loop_schedule=True,
    )

    plan = provider.plan_at("gov", _U36_START + timedelta(seconds=offset))

    assert plan is not None
    segment = plan.segments[0]
    assert segment.duration_seconds == pytest.approx(expected_duration)
    assert segment.playable_duration_seconds == pytest.approx(37.987822)
    assert segment.schedule_occurrence_start == _U36_START + timedelta(seconds=occurrence_offset)
    assert (segment.inpoint_seconds or 0) + segment.duration_seconds == pytest.approx(37.987822)


@pytest.mark.parametrize(
    ("stored_duration", "slot", "media_end", "trim_in", "trim_out", "expected"),
    [
        (37, 36, 37.987822, None, None, 36.0),  # Slot remains a hard limit.
        (40, 38, 37.987822, None, None, 37.987822),  # Real end caps an overstated row.
        (37, 38, None, None, None, 37.0),  # No probe preserves the row-only fallback.
        (37, 38, 37.987822, 0.0, 36.5, 36.5),  # Explicit trim is never extended.
        (37, 38, 37.987822, None, 36.5, 36.5),
        (37, 38, 37.987822, 2.0, None, 35.0),  # Preserve a start-only trim's limits.
        (37, 38, 37.987822, 2.0, 40.0, 35.987822),  # Trim cannot exceed real media.
        (37, 38, None, 2.0, 36.5, 34.5),
    ],
)
def test_precise_media_duration_preserves_slot_trim_and_probe_fallback_limits(
    tmp_path: Path,
    stored_duration: int,
    slot: int,
    media_end: float | None,
    trim_in: float | None,
    trim_out: float | None,
    expected: float,
) -> None:
    media = _media(tmp_path)
    asset = _asset_of(media, duration_seconds=stored_duration, trim_in=trim_in, trim_out=trim_out)

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[_schedule_item(scheduled_at=_U36_START, duration_seconds=slot)],
        asset_resolver=lambda _: asset,
        media_duration_resolver=lambda _: media_end,
        now=_U36_START,
    )

    assert plan is not None
    segment = plan.segments[0]
    assert segment.duration_seconds == pytest.approx(expected)
    if trim_out is not None:
        assert segment.outpoint_seconds <= trim_out
    if media_end is not None:
        assert (segment.inpoint_seconds or 0) + segment.duration_seconds <= media_end


def test_u36_the_media_probe_is_memoized_per_file_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The automation loop builds a plan every ~2s, so the probe must not
    re-spawn ffprobe per build (the media lives on a station NAS: a blocking
    probe per poll would stall every rollover decision). One probe per file
    version, re-probed when the file changes."""

    calls: list[str] = []
    monkeypatch.setattr(
        source_plan_module,
        "probe_media_duration_seconds",
        _u36_truthful_probe(_U36_REAL_MEDIA, calls),
        raising=False,
    )
    provider = _u36_provider(
        tmp_path,
        _u36_two_short_programs(),
        recorded_seconds={"weather": 180.0, "council": 120.0},
    )

    provider.plan_at("gov", _U36_START)
    assert calls == ["weather.ts"]

    provider.plan_at("gov", _U36_START + timedelta(seconds=5))
    assert calls == ["weather.ts"], "a probe per plan build was re-spawned"

    (tmp_path / "weather.ts").write_text("fake-but-longer", encoding="utf-8")
    provider.plan_at("gov", _U36_START + timedelta(seconds=9))
    assert calls == ["weather.ts", "weather.ts"], "a changed file was not re-probed"


def test_u36_a_probe_that_cannot_answer_leaves_the_row_duration_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open, pinned: a station without ffprobe (or media it cannot read)
    keeps EXACTLY today's row-derived behavior. The defect can still recur
    there -- that is the honest residual, not a silent new failure mode, and
    the planner has never had any other number to plan from."""

    monkeypatch.setattr(
        source_plan_module, "probe_media_duration_seconds", lambda _path: None, raising=False
    )
    provider = _u36_provider(
        tmp_path,
        _u36_two_short_programs(),
        recorded_seconds={"weather": 180.0, "council": 120.0},
    )

    at_start = provider.plan_at("gov", _U36_START)
    assert at_start is not None
    assert at_start.segments[0].duration_seconds == pytest.approx(180.0)

    past_the_end = provider.plan_at("gov", _U36_START + timedelta(seconds=178))
    assert past_the_end is not None
    assert past_the_end.segments[0].source_ref == "weather"
    assert past_the_end.segments[0].inpoint_seconds == pytest.approx(178.0)
    assert past_the_end.segments[0].duration_seconds == pytest.approx(2.0)


def test_u36_the_direct_builder_never_probes_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bare builder keeps its row-only contract.

    Every existing caller of `build_source_plan_from_schedule` (the module's own
    ~50 test cases and any future one) is deterministic and ffprobe-free; the
    media probe is wired in at the PROVIDER, which is the shape both production
    sites construct (automation.py:2628, cli.py:1150). This test exists so that
    boundary cannot move silently.
    """

    def loud_probe(_path: Path) -> float | None:
        raise AssertionError("the direct builder must not probe media")

    monkeypatch.setattr(
        source_plan_module, "probe_media_duration_seconds", loud_probe, raising=False
    )
    media = tmp_path / "program.ts"
    media.write_text("fake", encoding="utf-8")

    plan = build_source_plan_from_schedule(
        channel_id="gov",
        schedule_items=[_schedule_item(scheduled_at=_U36_START, duration_seconds=180)],
        asset_resolver=lambda asset_id: _asset(media, asset_id=asset_id),
        now=_U36_START,
    )

    assert plan is not None
    # `_asset`'s rows: trim_out 120 - trim_in 10 = 110s playable, inside a
    # 180s slot -> D42's min(slot, playable). Row-derived, exactly as before.
    assert plan.segments[0].duration_seconds == pytest.approx(110.0)
