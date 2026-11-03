# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for egress source preparation."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import civiccast.egress.preparer as preparer_module
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.loudness_ride import (
    LeveledAttempt,
    LeveledSelection,
    LeveledWindow,
    LoudnessRideCancelledError,
    LoudnessRideError,
    RideCurve,
    RideParams,
)
from civiccast.egress.models import (
    CanonicalProfile,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
)
from civiccast.egress.preparer import (
    SourcePreparationCancelledError,
    SourcePreparer,
    build_conform_source_args,
    build_loudnorm_probe_args,
    preparation_timeout_seconds_from_env,
)
from civiccast.stream._ffmpeg import FfmpegResult
from civiccast.stream.loudness import LoudnessGateResult


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        slate_message="CivicCast is preparing the channel.",
        loudness_target_lufs=-24.0,
        loudness_tolerance_lufs=1.0,
        canonical_profile=CanonicalProfile(width=640, height=360, video_bitrate_kbps=1200),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/out.ts")],
    )


def test_cold_compatible_video_is_copied_while_audio_is_conformed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        preparer_module, "probe_canonical_video", lambda *a, **kw: True, raising=False
    )
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda *a: 90.0)
    source = tmp_path / "compatible.mp4"
    source.write_bytes(b"media")
    calls = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **kw: _loudness(),
        warm_scheduler=lambda job: None,
    )
    plan = EgressSourcePlan(
        channel_id="gov",
        segments=[EgressSourceSegment(label="Compatible", path=str(source), duration_seconds=90)],
    )
    preparer.prepare(plan, _config())
    args = calls[0]
    assert args[args.index("-c:v") + 1] == "copy"
    assert "-vf" not in args
    assert args[args.index("-c:a") + 1] == "aac"


def test_recoverable_warm_failure_retries_within_minutes(tmp_path, monkeypatch):
    preparer, jobs, source, key, config = _u29_warm_fixture(
        tmp_path, monkeypatch, lambda *a, **kw: (_ for _ in ()).throw(OSError("temporarily busy"))
    )
    clock = _FakeClock()
    monkeypatch.setattr(preparer_module, "time", clock)
    preparer._schedule_warm(key, source, config, _loudness(), False, media_duration_seconds=60)
    jobs[0]()
    assert 30 <= preparer._warm_backoff_until[key] - clock.monotonic() <= 300
    first_delay = preparer._warm_backoff_until[key] - clock.monotonic()
    clock.advance(first_delay + 1)
    preparer._schedule_warm(key, source, config, _loudness(), False, media_duration_seconds=60)
    jobs[1]()
    assert preparer._warm_backoff_until[key] - clock.monotonic() == first_delay * 2
    for _ in range(8):
        assert preparer._record_warm_failure(key) <= 1800


def test_streamcopy_never_bypasses_accurate_inpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(preparer_module, "probe_canonical_video", lambda *a, **kw: True)
    preparer = SourcePreparer(work_dir=tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    segment = EgressSourceSegment(
        label="Trim", path=str(source), inpoint_seconds=0.5, duration_seconds=2
    )
    assert not preparer._copy_video(source, _config().canonical_profile, segment)


def test_video_copy_preserves_loudness_processing(tmp_path):
    args = build_conform_source_args(
        source_path=tmp_path / "source.mp4",
        output_path=tmp_path / "out.ts",
        segment=None,
        profile=_config().canonical_profile,
        loudness_target_lufs=-24,
        copy_video=True,
    )
    assert args[args.index("-c:v") + 1] == "copy"
    assert "-vf" not in args
    assert "loudnorm" in args[args.index("-af") + 1]


def test_copy_decision_survives_cache_eviction_between_lookup_steps(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    profile = _config().canonical_profile
    stat = source.stat()
    key = (str(source), stat.st_size, stat.st_mtime_ns, profile.width, profile.height, profile.fps)

    class EvictedAfterContains(dict):
        def __contains__(self, key):
            found = super().__contains__(key)
            self.clear()  # deterministic competing-thread eviction after membership
            return found

    preparer._video_copy_checks = EvictedAfterContains({key: True})
    monkeypatch.setattr(preparer_module, "probe_canonical_video", lambda *a, **kw: True)
    assert preparer._copy_video(source, profile, None) is True


def test_default_warm_queue_prioritizes_upcoming_work(monkeypatch):
    import queue

    monkeypatch.setattr(preparer_module, "_warm_worker_started", True)
    monkeypatch.setattr(preparer_module, "_warm_queue", queue.PriorityQueue())

    def background():
        pass

    def upcoming():
        pass

    upcoming._warm_priority = 0
    preparer_module._default_warm_scheduler(background)
    preparer_module._default_warm_scheduler(upcoming)
    queued = preparer_module._warm_queue.get_nowait()
    assert isinstance(queued, tuple) and queued[-1] is upcoming


def test_upcoming_plan_promotes_an_already_queued_background_warm(tmp_path, monkeypatch):
    import queue

    monkeypatch.setattr(preparer_module, "_warm_worker_started", True)
    monkeypatch.setattr(preparer_module, "_warm_queue", queue.PriorityQueue())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    preparer._schedule_warm(key, source, config, _loudness(), False, media_duration_seconds=60)
    segment = EgressSourceSegment(label="Upcoming", path=str(source), duration_seconds=60)
    preparer._schedule_plan_warm(config, segment)
    assert preparer_module._warm_queue.qsize() == 1
    assert preparer_module._warm_queue.get_nowait()[0] == 0


def _source_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=12.5,
                inpoint_seconds=2,
                outpoint_seconds=14.5,
            )
        ],
    )


def _loudness(status: str = "ok", measured_lufs: float | None = -24.1) -> LoudnessGateResult:
    return LoudnessGateResult(
        status=status,
        standard="ITU-R BS.1770 / EBU R128",
        target_lufs=-24.0,
        used_ffmpeg_wrapper=True,
        measured_lufs=measured_lufs,
        operator_action="test action",
    )


def test_build_conform_source_args_uses_canonical_profile_and_trim(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "prepared.ts"
    segment = EgressSourceSegment(
        label="Council meeting",
        path=str(source),
        duration_seconds=10,
        inpoint_seconds=5,
        outpoint_seconds=15,
    )

    args = build_conform_source_args(
        source_path=source,
        output_path=output,
        segment=segment,
        profile=_config().canonical_profile,
    )

    assert args[:7] == ["-hide_banner", "-loglevel", "warning", "-ss", "5", "-i", str(source)]
    assert args[7:9] == ["-t", "10"]
    assert "scale=640:360" in " ".join(args)
    assert "1200k" in args
    assert "-af" not in args
    assert args[-3:] == ["-f", "mpegts", str(output)]


def _loudnorm_probe_stderr(**over: object) -> str:
    """A realistic single-pass loudnorm JSON block for probe stubs."""

    base = {
        "input_i": "-23.01",
        "input_tp": "-0.05",
        "input_lra": "22.90",
        "input_thresh": "-35.18",
        "target_offset": "0.83",
    }
    base.update(over)
    return json.dumps(base, indent=4)


def _write_fake_output(args: list[str]) -> None:
    """H5 fix: prepared-segment writes are now atomic (tmp + rename), so a fake
    ffmpeg runner must actually create the file ``args[-1]`` names (its
    ``.tmp`` sibling of the final prepared path) for the rename to succeed --
    mirrors what a real ffmpeg process does."""
    Path(args[-1]).write_text("prepared", encoding="utf-8")


def test_prepare_uses_explicit_protected_plan_snapshot(tmp_path: Path) -> None:
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    preparer.set_protected_plan_dirs_provider(
        lambda _channel_id: (_ for _ in ()).throw(AssertionError("provider must not run"))
    )
    live_plan = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Live",
                path="srt://127.0.0.1:19002",
                duration_seconds=60,
                kind="live",
            )
        ],
    )

    report = preparer.prepare(live_plan, _config(), protected_plan_dirs=frozenset())

    assert report.source_plan == live_plan


def test_prepare_cancellation_after_legacy_fake_runner_cleans_plan_dir(tmp_path: Path) -> None:
    cancel_event = threading.Event()

    def runner(args: list[str]) -> FfmpegResult:
        _write_fake_output(args)
        cancel_event.set()
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda _job: None,
    )

    with pytest.raises(SourcePreparationCancelledError):
        preparer.prepare(
            _source_plan(tmp_path),
            _config(),
            cancel_event=cancel_event,
            protected_plan_dirs=frozenset(),
        )

    assert not list((tmp_path / "work" / "gov" / "prepared").iterdir())


def test_source_preparer_conforms_inside_loudness_tolerance(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=lambda args: (
            (captured.setdefault("args", args) and _write_fake_output(args))
            or FfmpegResult(returncode=0, stdout="", stderr="")
        ),
        loudness_checker=lambda **kwargs: captured.setdefault("loudness", kwargs) and _loudness(),
        warm_scheduler=lambda job: None,  # keep the at-air behavior deterministic
    )

    source_plan = _source_plan(tmp_path)
    occurrence = datetime(2026, 10, 30, tzinfo=UTC)
    source_segment = source_plan.segments[0].model_copy(
        update={
            "schedule_occurrence_id": "schedule-item-1",
            "schedule_occurrence_start": occurrence,
        }
    )
    report = preparer.prepare(
        source_plan.model_copy(update={"segments": [source_segment]}), _config()
    )

    assert report.source_plan.channel_id == "gov"
    prepared = report.source_plan.segments[0]
    # H5 fix: every prepare() call now writes into its own uniquely-named
    # subdirectory under <channel>/prepared/ (a 12-hex-char uuid segment)
    # instead of a fixed, collision-prone path -- match that shape rather
    # than a single hardcoded path.
    assert re.search(r"gov[\\/]prepared[\\/][0-9a-f]{12}[\\/]segment-0001\.ts$", prepared.path), (
        prepared.path
    )
    assert prepared.inpoint_seconds is None
    assert prepared.outpoint_seconds is None
    assert prepared.schedule_occurrence_id == "schedule-item-1"
    assert prepared.schedule_occurrence_start == occurrence
    assert report.records[0].normalized is False
    assert "-af" not in captured["args"]
    assert captured["loudness"]["target_lufs"] == -24.0


def test_repeated_occurrences_keep_identity_through_prepare_reuse_and_graph(tmp_path: Path) -> None:
    from civiccast.egress.gst.bridge import graph_from_config

    calls = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    source = _source_plan(tmp_path).segments[0]
    start = datetime(2026, 10, 30, tzinfo=UTC)
    segments = [
        source.model_copy(
            update={
                "schedule_occurrence_id": "same-row",
                "schedule_occurrence_start": start + timedelta(seconds=38 * i),
                "source_ref": "same-asset",
                "source_offset_seconds": offset,
            }
        )
        for i, offset in enumerate((3.583484, 3.614415, 6.251983))
    ]
    prepared = preparer.prepare(EgressSourcePlan(channel_id="gov", segments=segments), _config())
    assert len(calls) == 1, "identical media should still reuse the preparation"
    graph = graph_from_config(_config(), prepared.source_plan)
    metadata = graph.sources[0].subchain_sources
    assert [x["schedule_occurrence_start"] for x in metadata] == [
        s.schedule_occurrence_start.isoformat() for s in segments
    ]
    assert [x["source_offset_seconds"] for x in metadata] == [
        str(s.source_offset_seconds) for s in segments
    ]


def test_source_preparer_passes_live_segments_through_without_local_file_check(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=lambda args: captured.setdefault("args", args),  # type: ignore[return-value]
        loudness_checker=lambda **kwargs: captured.setdefault("loudness", kwargs),  # type: ignore[return-value]
    )
    source_plan = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Live: Council chamber",
                path="srt://127.0.0.1:19002",
                duration_seconds=60,
                kind="live",
                source_ref="gov:relay",
            )
        ],
    )

    report = preparer.prepare(source_plan, _config())

    assert report.source_plan.segments == source_plan.segments
    assert report.records[0].loudness_status == "not_checked_live_passthrough"
    assert report.records[0].prepared_path == "srt://127.0.0.1:19002"
    assert captured == {}


def test_source_preparer_normalizes_when_loudness_is_out_of_tolerance(tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_loudnorm_probe_aware(
            lambda args: (
                (captured.setdefault("args", args) and _write_fake_output(args))
                or FfmpegResult(returncode=0, stdout="", stderr="")
            )
        ),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda job: None,  # keep the at-air behavior deterministic
    )

    report = preparer.prepare(_source_plan(tmp_path), _config())

    assert report.records[0].normalized is True
    assert "-af" in captured["args"]
    assert "loudnorm=I=-24" in " ".join(captured["args"])


def test_source_preparer_fails_when_loudness_cannot_be_measured(tmp_path: Path) -> None:
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=None),
    )

    with pytest.raises(SourcePrepareError, match="could not be measured"):
        preparer.prepare(_source_plan(tmp_path), _config())


def test_source_preparer_fails_when_conform_ffmpeg_fails(tmp_path: Path) -> None:
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=lambda _args: FfmpegResult(returncode=1, stdout="", stderr="boom"),
        loudness_checker=lambda **_kwargs: _loudness(),
    )

    with pytest.raises(SourcePrepareError, match="could not be conformed"):
        preparer.prepare(_source_plan(tmp_path), _config())


def test_source_preparer_conform_timeout_fails_closed_and_cleans_partial_output(
    tmp_path: Path,
) -> None:
    def timed_out_runner(_args: list[str]) -> FfmpegResult:
        raise subprocess.TimeoutExpired(["ffmpeg"], timeout=3)

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=timed_out_runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        preparation_timeout_seconds=3.0,
    )

    with pytest.raises(SourcePrepareError, match="timed out after 3s"):
        preparer.prepare(_source_plan(tmp_path), _config())

    prepared_root = tmp_path / "work" / "gov" / "prepared"
    leftovers = [path for path in prepared_root.rglob("*") if path.is_file()]
    assert leftovers == []


class TestPreparationTimeoutEnvSpelling:
    """BETA.10 U03. ``preparation_timeout_seconds_from_env`` read only the
    one-C ``CIVICAST_EGRESS_PREPARATION_TIMEOUT_SECONDS`` while the station's
    service registry (``HKLM\\SYSTEM\\CurrentControlSet\\Services\\
    CivicCastSupervisor``'s ``Environment``) sets the two-C
    ``CIVICCAST_EGRESS_PREPARATION_TIMEOUT_SECONDS`` -- so the station's 300
    was never read. These cover both spellings, the precedence between them,
    and that the invalid-value guard still holds for whichever one supplied
    the value.
    """

    _PRIMARY = "CIVICCAST_EGRESS_PREPARATION_TIMEOUT_SECONDS"
    _LEGACY = "CIVICAST_EGRESS_PREPARATION_TIMEOUT_SECONDS"
    _LOGGER = "civiccast.egress.preparer"

    @pytest.fixture(autouse=True)
    def _fresh_one_time_latch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The deprecation/conflict warnings are one-time per service lifetime,
        # so the latch has to start clean for each test that asserts on them.
        # raising=False so the same fixture also runs against the
        # pre-U03 module in the red-first demonstration, where this
        # latch does not exist yet.
        monkeypatch.setattr(preparer_module, "_RENAMED_ENV_WARNED", set(), raising=False)

    def test_the_default_is_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(self._PRIMARY, raising=False)
        monkeypatch.delenv(self._LEGACY, raising=False)
        assert preparation_timeout_seconds_from_env() > 0

    def test_the_registry_spelling_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """RED before this change: with only the two-C name set the reader
        returned the 300s default and never saw the 120 here."""

        monkeypatch.delenv(self._LEGACY, raising=False)
        monkeypatch.setenv(self._PRIMARY, "120")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert preparation_timeout_seconds_from_env() == 120.0

        assert caplog.records == []

    def test_the_legacy_spelling_still_works_and_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv(self._PRIMARY, raising=False)
        monkeypatch.setenv(self._LEGACY, "450")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert preparation_timeout_seconds_from_env() == 450.0
            assert preparation_timeout_seconds_from_env() == 450.0

        deprecations = [record for record in caplog.records if self._LEGACY in record.getMessage()]
        assert len(deprecations) == 1, [record.getMessage() for record in caplog.records]
        assert self._PRIMARY in deprecations[0].getMessage()

    def test_the_registry_spelling_wins_and_both_values_are_named(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(self._LEGACY, "450")
        monkeypatch.setenv(self._PRIMARY, "120")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert preparation_timeout_seconds_from_env() == 120.0
            assert preparation_timeout_seconds_from_env() == 120.0

        conflicts = [record for record in caplog.records if "both set" in record.getMessage()]
        assert len(conflicts) == 1, [record.getMessage() for record in caplog.records]
        message = conflicts[0].getMessage()
        assert "120" in message and "450" in message
        assert self._PRIMARY in message and self._LEGACY in message

    def test_both_spellings_agreeing_is_not_reported(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(self._LEGACY, "450")
        monkeypatch.setenv(self._PRIMARY, "450")

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert preparation_timeout_seconds_from_env() == 450.0

        assert caplog.records == []

    def test_a_blank_registry_value_falls_through_to_the_legacy_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A whitespace-only two-C value counts as unset, so an empty line in
        an env file cannot mask a real value at the other spelling."""

        monkeypatch.setenv(self._PRIMARY, "   ")
        monkeypatch.setenv(self._LEGACY, "450")
        assert preparation_timeout_seconds_from_env() == 450.0

    @pytest.mark.parametrize("bad", ["bad", "-5", "0"])
    def test_an_invalid_value_falls_back_and_names_the_spelling_used(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
    ) -> None:
        monkeypatch.delenv(self._LEGACY, raising=False)
        monkeypatch.setenv(self._PRIMARY, bad)

        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert preparation_timeout_seconds_from_env() == 300.0

        assert any(self._PRIMARY in record.getMessage() for record in caplog.records), [
            record.getMessage() for record in caplog.records
        ]


def test_source_preparer_rejects_missing_source(tmp_path: Path) -> None:
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    plan = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Missing",
                path=str(tmp_path / "missing.mp4"),
                duration_seconds=10,
            )
        ],
    )

    with pytest.raises(SourcePrepareError, match="missing before preparation"):
        preparer.prepare(plan, _config())


def test_same_path_with_different_trims_prepares_separately(tmp_path: Path) -> None:
    """Audit TEST-005: the memo key is (path, inpoint, outpoint); collapsing
    it to path alone would air the FIRST trim for every slot - exactly what
    join-in-progress plans (same asset, different offsets) would produce."""

    source = tmp_path / "council.ts"
    source.write_text("fake media", encoding="utf-8")

    def segment(inpoint: float, outpoint: float) -> EgressSourceSegment:
        return EgressSourceSegment(
            label=f"Council {inpoint:g}",
            path=str(source),
            duration_seconds=outpoint - inpoint,
            inpoint_seconds=inpoint,
            outpoint_seconds=outpoint,
        )

    plan = EgressSourcePlan(channel_id="gov", segments=[segment(0, 10), segment(10, 20)])
    ffmpeg_calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        ffmpeg_calls.append(args)
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path,
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,  # at-air conforms only; warms tested separately
    )

    report = preparer.prepare(plan, _config())

    assert len(ffmpeg_calls) == 2  # two distinct conforms
    assert len({seg.path for seg in report.source_plan.segments}) == 2


def test_reloading_a_plan_never_touches_the_previous_plans_prepared_files(
    tmp_path: Path,
) -> None:
    """H5 (measured on tester hardware): for the GStreamer engine
    (``playout_trim_supported=False``, the default), a content-reload's
    ``prepare()`` call used to write its trimmed-miss conform output to the SAME
    fixed path (``<channel>/prepared/segment-0001.ts``, keyed only by segment
    index within its own call) that the FIRST prepare() call had already written
    -- while the worker airing the first plan's segment could still be reading
    that exact file. Every ``prepare()`` call now gets its own uniquely-named
    subdirectory, so a second call for a DIFFERENT plan (even one whose segment
    lands at the same index, 1, as the first) can never share an output path
    with -- let alone overwrite -- the first call's still-possibly-live file."""

    def _segment(path: Path, label: str) -> EgressSourceSegment:
        path.write_text("fake media", encoding="utf-8")
        return EgressSourceSegment(
            label=label,
            path=str(path),
            duration_seconds=10,
            inpoint_seconds=1,  # forces the trimmed-miss (non-cached) write path
            outpoint_seconds=11,
        )

    def runner(args: list[str]) -> FfmpegResult:
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path,
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )

    plan_a = EgressSourcePlan(
        channel_id="gov", segments=[_segment(tmp_path / "a.ts", "Plan A segment 1")]
    )
    report_a = preparer.prepare(plan_a, _config())
    prepared_a = Path(report_a.source_plan.segments[0].path)
    assert prepared_a.is_file()
    bytes_before = prepared_a.read_bytes()
    mtime_before = prepared_a.stat().st_mtime_ns

    # A second, DIFFERENT plan -- e.g. a content-reload's newly-due program --
    # for the SAME channel. Its one segment also lands at index 1.
    plan_b = EgressSourcePlan(
        channel_id="gov", segments=[_segment(tmp_path / "b.ts", "Plan B segment 1")]
    )
    report_b = preparer.prepare(plan_b, _config())
    prepared_b = Path(report_b.source_plan.segments[0].path)

    assert prepared_b != prepared_a  # never the same path
    assert prepared_b.parent != prepared_a.parent  # never even the same plan directory
    # Plan A's already-prepared file is completely untouched by preparing plan B.
    assert prepared_a.is_file()
    assert prepared_a.read_bytes() == bytes_before
    assert prepared_a.stat().st_mtime_ns == mtime_before


def test_trimmed_miss_conform_write_is_atomic(tmp_path: Path) -> None:
    """H5 (atomic write): a failed conform must leave no ``.tmp`` sibling behind
    at the prepared segment's final path, and a successful one must leave ONLY
    the final path (never the ``.tmp`` alongside it)."""
    source = tmp_path / "council.ts"
    source.write_text("fake media", encoding="utf-8")
    segment = EgressSourceSegment(
        label="Council meeting",
        path=str(source),
        duration_seconds=10,
        inpoint_seconds=1,
        outpoint_seconds=11,
    )
    plan = EgressSourcePlan(channel_id="gov", segments=[segment])

    # Failure case: the runner never creates the .tmp output file at all (the
    # real ffmpeg failure mode this simulates), and returns nonzero.
    failing_preparer = SourcePreparer(
        work_dir=tmp_path / "fail-work",
        ffmpeg_runner=lambda _args: FfmpegResult(returncode=1, stdout="", stderr="boom"),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    with pytest.raises(SourcePrepareError, match="could not be conformed"):
        failing_preparer.prepare(plan, _config())
    fail_prepared_root = tmp_path / "fail-work" / "gov" / "prepared"
    leftover_files = (
        [p for p in fail_prepared_root.rglob("*") if p.is_file()]
        if fail_prepared_root.exists()
        else []
    )
    assert leftover_files == []  # no partial/.tmp file survives a failed conform

    # Success case: only the final path exists afterward, never a lingering .tmp.
    def runner(args: list[str]) -> FfmpegResult:
        assert args[-1].endswith(".ts.tmp")  # the atomic-write staging name
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    ok_preparer = SourcePreparer(
        work_dir=tmp_path / "ok-work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    report = ok_preparer.prepare(plan, _config())
    prepared_path = Path(report.source_plan.segments[0].path)
    plan_dir = prepared_path.parent
    assert sorted(p.name for p in plan_dir.iterdir()) == [prepared_path.name]


def test_stale_prepared_plan_directories_are_garbage_collected(tmp_path: Path) -> None:
    """H5 (GC backstop): a per-plan directory older than the generous age floor
    is reclaimed on the NEXT ``prepare()`` call for that channel; one younger
    than the floor is left alone (it might still be airing)."""
    source = tmp_path / "council.ts"
    source.write_text("fake media", encoding="utf-8")
    segment = EgressSourceSegment(
        label="Council meeting",
        path=str(source),
        duration_seconds=10,
        inpoint_seconds=1,
        outpoint_seconds=11,
    )
    plan = EgressSourcePlan(channel_id="gov", segments=[segment])

    def runner(args: list[str]) -> FfmpegResult:
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )

    # F3 fix: keep-N-most-recent (N=3) protects a directory from GC purely by
    # recency, however old it is -- so proving the AGE FLOOR actually fires
    # requires enough other directories to push the stale one out of that
    # protection first. One dir older than the floor, plus enough OTHER dirs
    # (all younger) to exceed keep-N, plus a fresh one this very prepare()
    # call creates.
    prepared_root = tmp_path / "work" / "gov" / "prepared"
    stale_dir = prepared_root / ("0" * 12)
    stale_dir.mkdir(parents=True)
    (stale_dir / "segment-0001.ts").write_text("old", encoding="utf-8")
    old_time = time.time() - (25 * 3600)  # older than the 24h floor
    os.utime(stale_dir, (old_time, old_time))

    for i in range(preparer_module._PREPARED_PLAN_DIR_KEEP_N):
        other_dir = prepared_root / f"other{i:03d}"
        other_dir.mkdir(parents=True)
        (other_dir / "segment-0001.ts").write_text("other", encoding="utf-8")

    fresh_dir = prepared_root / ("1" * 12)
    fresh_dir.mkdir(parents=True)
    (fresh_dir / "segment-0001.ts").write_text("fresh", encoding="utf-8")
    # left at the current mtime -- younger than the floor, must survive

    preparer.prepare(plan, _config())

    remaining = {p.name for p in prepared_root.iterdir() if p.is_dir()}
    assert stale_dir.name not in remaining  # reclaimed: older than the floor,
    # and pushed out of keep-N-most-recent by the other directories above
    assert fresh_dir.name in remaining  # untouched: younger than the floor


def test_a_plan_dir_still_referenced_by_a_live_worker_is_never_deleted(
    tmp_path: Path,
) -> None:
    """F4: a per-plan directory the CALLER (the daemon, tracking which plan a
    live worker is currently reading) explicitly marks as still in use is
    NEVER removed by GC -- however old it is, however large (the byte budget
    alone would otherwise evict it), and however far outside the
    keep-N-most-recent protection it falls."""
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    prepared_root = tmp_path / "work" / "gov" / "prepared"

    live_dir = prepared_root / "aaaaaaaaaaaa"
    live_dir.mkdir(parents=True)
    # large enough that the byte budget alone would otherwise evict it first
    (live_dir / "segment-0001.ts").write_bytes(b"x" * 1024)
    ancient_time = time.time() - (48 * 3600)  # older than the 24h floor
    os.utime(live_dir, (ancient_time, ancient_time))

    # enough OTHER (newer) directories to push live_dir out of keep-N's
    # recency protection too -- ``keep`` must be the thing saving it, not an
    # accident of recency.
    for i in range(preparer_module._PREPARED_PLAN_DIR_KEEP_N + 2):
        other_dir = prepared_root / f"other{i:03d}"
        other_dir.mkdir(parents=True)
        (other_dir / "segment-0001.ts").write_text("other", encoding="utf-8")

    preparer._gc_prepared_plan_dirs(prepared_root, keep=frozenset({live_dir}))

    assert live_dir.exists()
    assert (live_dir / "segment-0001.ts").exists()


def test_release_reclaims_one_specific_plan_dir_immediately(tmp_path: Path) -> None:
    """F3: the daemon calls release() for a plan it independently knows has
    settled (the OLD plan of a just-committed content-reload) -- reclaimed
    immediately, no GC pass needed."""
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    plan_dir = tmp_path / "work" / "gov" / "prepared" / "abc123"
    plan_dir.mkdir(parents=True)
    (plan_dir / "segment-0001.ts").write_text("retired", encoding="utf-8")

    preparer.release(plan_dir)

    assert not plan_dir.exists()


def test_release_of_none_is_a_no_op(tmp_path: Path) -> None:
    """A plan whose prepare() never created a discrete directory (F7: a
    live-only plan, or every segment a playout_trim_supported cache hit)
    reports plan_dir=None -- release(None) must not raise."""
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    preparer.release(None)  # must not raise


def test_flat_pre_upgrade_prepared_files_are_swept(tmp_path: Path) -> None:
    """F6: a pre-upgrade flat ``prepared/segment-NNNN.ts`` (the fixed-path
    layout H5 replaced) sitting directly under the channel's prepared/ root
    is removed by GC -- nothing written after this fix ever reads from there
    again."""
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    prepared_root = tmp_path / "work" / "gov" / "prepared"
    prepared_root.mkdir(parents=True)
    flat_file = prepared_root / "segment-0001.ts"
    flat_file.write_text("pre-upgrade leftover", encoding="utf-8")
    flat_tmp = prepared_root / "segment-0002.ts.tmp"
    flat_tmp.write_text("pre-upgrade partial", encoding="utf-8")

    preparer._gc_prepared_plan_dirs(prepared_root)

    assert not flat_file.exists()
    assert not flat_tmp.exists()


def test_a_plan_with_no_local_writes_leaves_no_directory(tmp_path: Path) -> None:
    """F7: a plan whose every segment is live (no local ffmpeg write ever
    happens) must not leave an empty per-plan directory under prepared/, and
    the report's plan_dir is None (nothing to release or track)."""
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    plan = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Live feed",
                path="srt://127.0.0.1:19002",
                duration_seconds=60,
                kind="live",
                source_ref="gov:relay",
            )
        ],
    )

    report = preparer.prepare(plan, _config())

    assert report.plan_dir is None
    prepared_root = tmp_path / "work" / "gov" / "prepared"
    assert not prepared_root.exists() or list(prepared_root.iterdir()) == []


def test_a_plan_with_local_writes_reports_its_plan_dir(tmp_path: Path) -> None:
    """F7 counterpart: a plan that DOES write locally reports the real
    plan_dir, and the directory actually holds the written segment."""

    def runner(args: list[str]) -> FfmpegResult:
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )

    report = preparer.prepare(_source_plan(tmp_path), _config())

    assert report.plan_dir is not None
    assert report.plan_dir.is_dir()
    assert list(report.plan_dir.iterdir())


def test_repeated_identical_segments_prepare_once(tmp_path: Path) -> None:
    """CA-8: filler plans repeat one rendered segment hundreds of times to
    span the fill target; preparing (loudness probe + conform) per ENTRY
    multiplied channel startup ~120x. Identical (path, trim) segments must
    prepare exactly once and share the prepared output."""

    source = tmp_path / "slate.ts"
    source.write_text("fake media", encoding="utf-8")
    segment = EgressSourceSegment(
        label="CivicCast slate",
        path=str(source),
        duration_seconds=30,
        kind="slate",
        source_ref="civiccast-slate",
    )
    plan = EgressSourcePlan(channel_id="gov", segments=[segment] * 5)

    loudness_calls: list[dict] = []
    ffmpeg_calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        ffmpeg_calls.append(args)
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path,
        ffmpeg_runner=runner,
        loudness_checker=lambda **kwargs: loudness_calls.append(kwargs) or _loudness(),
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )

    report = preparer.prepare(plan, _config())

    assert len(loudness_calls) == 1
    assert len(ffmpeg_calls) == 1
    assert len(report.source_plan.segments) == 5
    assert len({seg.path for seg in report.source_plan.segments}) == 1


# ---------------------------------------------------------------------------
# #156 — persistent conform cache (long-asset TRANSITIONING stall)
# ---------------------------------------------------------------------------


def _untrimmed_plan(tmp_path: Path, label: str = "Council meeting") -> EgressSourcePlan:
    source = tmp_path / "long-recording.mp4"
    if not source.exists():
        source.write_text("fake long media", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[EgressSourceSegment(label=label, path=str(source), duration_seconds=3600.0)],
    )


def _loudnorm_probe_aware(runner):
    """Answer the two-pass loudnorm probe, then delegate to the wrapped runner."""

    def wrapped(args: list[str]) -> FfmpegResult:
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        return runner(args)

    return wrapped


def _counting_runner(calls: list[list[str]]):
    def runner(args: list[str]) -> FfmpegResult:
        calls.append(args)
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    return runner


# ---------------------------------------------------------------------------
# U25 -- the preparer's conform paths ride first, and fall back to loudnorm
# ---------------------------------------------------------------------------


def _no_ride(**_kwargs: object) -> LeveledWindow:
    """A ``level_window`` stand-in that is never available.

    Pre-U25 tests that assert on the loudnorm shapes use this: the preparer's
    ride is a real subprocess spawner, so a test that only cares about the
    loudnorm path must take the documented degradation path explicitly rather
    than shelling out to FFmpeg against a ``"fake media"`` file.
    """
    raise LoudnessRideError("test: the ride is not available")


class _StubLeveler:
    """A ``level_window`` stand-in that records its call and writes the artifact.

    The signature is spelled out rather than ``**kwargs`` so a test asserting on
    what the preparer passed also pins the interface the preparer calls it with.
    """

    def __init__(
        self,
        *,
        warning: str | None = None,
        hard_tp_met: bool = True,
        rides: int | None = None,
        cancel_on_call: int | None = None,
        write_audio: bool = True,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._warning = warning
        self._hard_tp_met = hard_tp_met
        self._rides = rides
        self._cancel_on_call = cancel_on_call
        self._write_audio = write_audio

    def __call__(
        self,
        *,
        source_path: Path,
        audio_path: Path,
        params: RideParams,
        profile: CanonicalProfile,
        duration_s: float | None = None,
        segment: EgressSourceSegment | None = None,
        pcm_path: Path | None = None,
        threads: int | None = None,
        ride_runner: object = None,
        reencode_runner: object = None,
        cancel_event: threading.Event | None = None,
        timeout_s: float | None = None,
    ) -> LeveledWindow:
        index = len(self.calls)
        self.calls.append(
            {
                "source_path": source_path,
                "audio_path": audio_path,
                "params": params,
                "profile": profile,
                "duration_s": duration_s,
                "segment": segment,
                "pcm_path": pcm_path,
                "threads": threads,
                "ride_runner": ride_runner,
                "reencode_runner": reencode_runner,
                "cancel_event": cancel_event,
                "timeout_s": timeout_s,
            }
        )
        if self._write_audio:
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"ride audio")
        if self._cancel_on_call == index:
            raise LoudnessRideCancelledError("test: cancelled mid-ride")
        if self._rides is not None and index >= self._rides:
            raise LoudnessRideError("test: the ride is not available")
        attempt = LeveledAttempt(
            round_index=0,
            limit_dbtp=-1.5,
            emitted_dbtp=-1.4,
            emitted_lufs=-24.1,
            worst_window_err_lu=0.2,
            whole_err_lu=-0.1,
            wall_s=0.5,
            decoded_peak_dbfs=-0.6,
        )
        return LeveledWindow(
            selection=LeveledSelection(
                kept=attempt,
                attempts=[attempt],
                target_met=True,
                hard_tp_met=self._hard_tp_met,
                warning=self._warning,
            ),
            curve=RideCurve(
                curve=[],
                trim_db=-2.2,
                iterations=2,
                converged=True,
                stop_reason="stop_lu",
                diagnostics=[],
            ),
            audio_path=audio_path,
            wall_s=0.5,
            tee_path=audio_path.with_name(audio_path.name + ".pcm"),
        )


def test_aired_before_asset_prepares_with_zero_ffmpeg_work(tmp_path: Path) -> None:
    """#156 acceptance 1: a program whose asset has aired before starts within
    seconds — the SECOND airing (fresh preparer instance, like a fresh hourly
    plan) must run zero conforms and zero loudness probes."""

    calls_1: list[list[str]] = []
    probes: list[dict] = []
    SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls_1),
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: job(),
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    ).prepare(_untrimmed_plan(tmp_path), _config())
    assert len(calls_1) == 1  # first airing conforms once (into the cache)

    calls_2: list[list[str]] = []
    report = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls_2),
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: job(),
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    ).prepare(_untrimmed_plan(tmp_path), _config())

    assert calls_2 == []  # zero ffmpeg work on the second airing
    assert len(probes) == 1  # loudness probed once, cached thereafter
    assert "conform-cache" in report.source_plan.segments[0].path


def test_join_in_progress_hits_cache_with_playout_trim(tmp_path: Path) -> None:
    """#156 acceptance 3: join-in-progress offsets survive — a cached full
    conform is aired with the segment's own inpoint applied at playout, so two
    different join offsets share one cache object with different trims."""

    warm_jobs: list = []
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")

    def plan(inpoint: float, occurrence_id: str) -> EgressSourcePlan:
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label="Council joined late",
                    path=str(source),
                    duration_seconds=1800.0,
                    inpoint_seconds=inpoint,
                    outpoint_seconds=inpoint + 1800.0,
                    schedule_occurrence_id=occurrence_id,
                )
            ],
        )

    # First-ever airing: trimmed conform straight to air + a warm scheduled.
    preparer.prepare(plan(120.0, "schedule-first"), _config())
    assert len(calls) == 1
    assert "-ss" in calls[0]  # air path still trims at the source (old latency)
    assert len(warm_jobs) == 1
    warm_jobs[0]()  # the background warm completes

    # Next hourly airing at a DIFFERENT join offset: cache hit, trim at playout.
    calls.clear()
    report = preparer.prepare(plan(300.0, "schedule-second"), _config())
    assert calls == []  # zero conforms
    seg = report.source_plan.segments[0]
    assert "conform-cache" in seg.path
    assert seg.inpoint_seconds == 300.0
    assert seg.outpoint_seconds == 300.0 + 1800.0
    assert seg.schedule_occurrence_id == "schedule-second"


def test_full_asset_warm_conform_has_no_trim_and_is_single_threaded(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    warm_jobs: list = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )
    preparer.prepare(_source_plan(tmp_path), _config())  # trimmed miss
    warm_jobs[0]()

    warm_args = calls[-1]
    assert "-ss" not in warm_args  # the cache unit is the FULL asset
    assert "-t" not in warm_args
    assert warm_args[warm_args.index("-threads") : warm_args.index("-threads") + 2] == [
        "-threads",
        "1",
    ]


def test_timed_out_full_asset_warm_removes_partial_tmp_output(tmp_path: Path) -> None:
    """A failed full-asset warm must not leave a multi-gigabyte partial cache file."""
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")

    def timed_out_runner(args: list[str]) -> FfmpegResult:
        Path(args[-1]).write_bytes(b"partial conform output")
        raise subprocess.TimeoutExpired(args, timeout=300.0)

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=timed_out_runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None

    with pytest.raises(SourcePrepareError, match="timed out"):
        preparer._conform_full_asset_into_cache(
            key,
            source,
            config,
            _loudness(),
            False,
        )

    assert not list((tmp_path / "work" / "conform-cache").glob("*.ts.tmp"))


def test_duplicate_warms_are_deduped(tmp_path: Path) -> None:
    warm_jobs: list = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )
    source = tmp_path / "raw-source.mp4"

    def plan(inpoint: float) -> EgressSourcePlan:
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label="x",
                    path=str(source),
                    duration_seconds=10.0,
                    inpoint_seconds=inpoint,
                    outpoint_seconds=inpoint + 10.0,
                )
            ],
        )

    source.write_text("fake media", encoding="utf-8")
    preparer.prepare(plan(1.0), _config())
    preparer.prepare(plan(2.0), _config())  # same asset, warm already pending

    assert len(warm_jobs) == 1


def test_schedule_warm_discards_key_when_scheduler_itself_fails(tmp_path: Path) -> None:
    """Item 66 round-6 (Opus review, point 7): if handing the warm job to
    ``self._warm_scheduler`` itself raises (the job never gets a chance to
    run, let alone clean up after itself), ``key`` must not be left stuck
    in ``self._warming`` forever -- that would silently suppress every
    GENUINE future warm for this asset for the life of this
    ``SourcePreparer`` instance."""

    def failing_scheduler(_job: Callable[[], None]) -> None:
        raise RuntimeError("scheduler is down")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=failing_scheduler,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None

    preparer._schedule_warm(key, source, config, _loudness(), False)  # must not raise

    assert key not in preparer._warming  # not left stuck -- a genuine warm can still be scheduled


def test_schedule_cache_copy_promotion_discards_key_when_scheduler_itself_fails(
    tmp_path: Path,
) -> None:
    """Companion for ``_schedule_cache_copy_promotion`` -- the exact same
    ``self._warming`` set, the exact same failure mode."""

    def failing_scheduler(_job: Callable[[], None]) -> None:
        raise RuntimeError("scheduler is down")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=failing_scheduler,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    finished = tmp_path / "already-airing.ts"
    finished.write_text("prepared bytes", encoding="utf-8")

    preparer._schedule_cache_copy_promotion(
        key, finished, source, _loudness(), False
    )  # must not raise

    assert key not in preparer._warming


def test_schedule_warm_job_reconforms_a_flagless_legacy_cache_entry(tmp_path: Path) -> None:
    """Item 66 round-9 (tests-only follow-up to the round-8 MEDIUM fix).
    ``_schedule_warm``'s queued ``_job`` must treat a flagless legacy meta
    (a pre-round-7 entry, or a probe-only write that never got a conform)
    as a MISS, not an already-populated hit -- see the round-8 comment right
    above the skip check in ``preparer.py`` for the self-heal failure this
    guards against. Plant exactly that shape (a short ``.ts`` alongside a
    meta with no ``full_asset_conform`` key at all) and prove the job
    actually re-conforms instead of returning early: reverting the skip
    predicate back to ``cached_meta is not None`` makes this fail, because
    that predicate alone is satisfied by the flagless meta and the job would
    return before ever calling the ffmpeg runner."""
    calls: list[list[str]] = []
    warm_jobs: list[Callable[[], None]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None

    preparer._schedule_warm(key, source, config, _loudness(), False)
    assert len(warm_jobs) == 1

    # Plant a flagless legacy entry -- exactly the pre-round-7 on-disk shape --
    # as if some earlier, now-stale run had left it there.
    cache_dir = tmp_path / "work" / "conform-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.ts").write_text("stale short fragment", encoding="utf-8")
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "loudness_status": "ok",
                "measured_lufs": -24.1,
                "normalized": False,
                "media_duration_seconds": None,
                # no "full_asset_conform" key at all -- the pre-round-7 shape
            }
        ),
        encoding="utf-8",
    )

    warm_jobs[0]()  # run the queued job now

    assert len(calls) == 1  # re-conformed -- the flagless entry was never trusted as a HIT
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["full_asset_conform"] is True  # the fresh conform's own promotion set it


def test_schedule_cache_copy_promotion_job_reconforms_a_flagless_legacy_cache_entry(
    tmp_path: Path,
) -> None:
    """Companion to the test above for ``_schedule_cache_copy_promotion``'s
    own queued ``_job`` -- the exact same round-8 MEDIUM fix, the exact same
    self-heal failure mode, a different job body (a byte-for-byte copy of an
    already-finished per-plan file rather than a fresh ffmpeg conform)."""
    warm_jobs: list[Callable[[], None]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    finished = tmp_path / "already-airing.ts"
    finished.write_text("the real, complete finished asset", encoding="utf-8")

    preparer._schedule_cache_copy_promotion(key, finished, source, _loudness(), False)
    assert len(warm_jobs) == 1

    # Plant a flagless legacy entry with SHORT, stale content -- if the job
    # wrongly treats this as already-populated, the copy never happens and
    # the stale bytes below survive unchanged.
    cache_dir = tmp_path / "work" / "conform-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.ts").write_text("stale short fragment", encoding="utf-8")
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "loudness_status": "ok",
                "measured_lufs": -24.1,
                "normalized": False,
                "media_duration_seconds": None,
                # no "full_asset_conform" key at all -- the pre-round-7 shape
            }
        ),
        encoding="utf-8",
    )

    warm_jobs[0]()  # run the queued job now

    cached_ts = cache_dir / f"{key}.ts"
    assert cached_ts.read_text(encoding="utf-8") == "the real, complete finished asset"
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["full_asset_conform"] is True


def test_corrupt_cache_sidecar_is_treated_as_miss(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: job(),
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )
    preparer.prepare(_untrimmed_plan(tmp_path), _config())
    # Corrupt the sidecar: the entry must not be trusted.
    for sidecar in (tmp_path / "work" / "conform-cache").glob("*.json"):
        sidecar.write_text("{not json", encoding="utf-8")

    calls.clear()
    preparer.prepare(_untrimmed_plan(tmp_path), _config())
    assert len(calls) == 1  # re-conformed, not served from the corrupt entry


def test_cache_eviction_respects_budget(tmp_path: Path, monkeypatch) -> None:
    """A budget too small even for the just-written entry evicts it before
    prepare() returns. Silently succeeding would send a nonexistent cache
    path into the source plan (dead air at encode time) -- prepare() must
    raise a clean SourcePrepareError instead."""
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0.000000001")  # ~1 byte
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: job(),
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )
    with pytest.raises(SourcePrepareError, match="budget"):
        preparer.prepare(_untrimmed_plan(tmp_path), _config())
    cache_dir = tmp_path / "work" / "conform-cache"
    assert list(cache_dir.glob("*.ts")) == []  # over budget -> evicted immediately


def test_conform_full_asset_into_cache_skips_instead_of_blocking_on_contention(
    tmp_path: Path,
) -> None:
    """automation.py shares one SourcePreparer across channels: a background
    warm and a foreground untrimmed-miss conform for the SAME asset (same
    cache key) can both reach _conform_full_asset_into_cache at once with no
    lock guarding it, both writing the identical {key}.ts.tmp -- concurrent
    calls for the same key must never collide.

    Item 66 round-4 (Opus review, point 1) changed HOW that's guaranteed:
    the per-key lock used to be acquired BLOCKING, so a contended second
    call would wait for the first to finish (serialized, but the FIRST
    version of this test's docstring called that the fix -- it was itself
    the bug: a synchronous caller blocking behind a background warm's
    entire single-threaded conform, tens of minutes for a long asset).
    The lock is now acquired NON-BLOCKING: a contended call returns
    ``None`` immediately instead of waiting, and never touches the ffmpeg
    runner at all."""
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")

    active = 0
    max_active = 0
    guard = threading.Lock()
    holder_started = threading.Event()
    proceed = threading.Event()

    def runner(args: list[str]) -> FfmpegResult:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        holder_started.set()
        proceed.wait(timeout=5)
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        with guard:
            active -= 1
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    loudness = _loudness()

    results: dict[int, Path | None] = {}

    def conform(index: int) -> None:
        results[index] = preparer._conform_full_asset_into_cache(
            key, source, config, loudness, False
        )

    t1 = threading.Thread(target=conform, args=(0,))
    t1.start()
    assert holder_started.wait(timeout=5)  # t1 genuinely holds the lock and is running

    # t2's call, made while t1 still holds the lock, must return None
    # immediately -- not wait, not run the ffmpeg runner.
    contended_result = preparer._conform_full_asset_into_cache(key, source, config, loudness, False)
    assert contended_result is None
    assert max_active == 1  # t2 never entered the runner -- never ran concurrently with t1

    proceed.set()
    t1.join(timeout=5)
    assert results[0] is not None  # t1 completed normally and populated the cache


def test_prepare_does_not_block_behind_a_warm_holding_the_cache_lock(tmp_path: Path) -> None:
    """Item 66 round-4 BLOCKER acceptance test (Opus review, point 1):
    reproduces the exact measured regression -- a synchronous prepare()
    took 3.01s behind a 3-second fake warm holding this asset's per-key
    lock -- and proves it is fixed. GStreamer engine (the constructor
    default): the untrimmed-miss path's cache promotion
    (_promote_finished_conform_into_cache) must not wait for the warm at
    all; the segment still airs from its own per-plan file regardless."""
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    key = preparer._cache_key(source, _config())
    assert key is not None
    lock = preparer._conform_lock(key)

    def fake_three_second_warm() -> None:
        with lock:
            time.sleep(3.0)

    warm_thread = threading.Thread(target=fake_three_second_warm, daemon=True)
    warm_thread.start()
    time.sleep(0.1)  # give the fake warm time to actually acquire the lock first

    started = time.monotonic()
    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())
    elapsed = time.monotonic() - started

    assert elapsed < 0.5  # measured regression: 3.01s; must not block behind the 3s warm
    seg = report.source_plan.segments[0]
    assert Path(seg.path).is_file()  # the segment still airs from its own per-plan file


def test_prepare_does_not_block_behind_a_warm_when_engine_can_trim(tmp_path: Path) -> None:
    """Companion (item 66 round-4, point 1's "Same at :995-998"): the
    playout_trim_supported=True call site (_conform_full_asset_into_cache
    invoked directly from _prepare_segment, not via the promotion helper)
    gets the same non-blocking treatment. Contention there falls through
    to the bounded per-segment conform instead of blocking on the lock
    too."""
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
        playout_trim_supported=True,
    )
    key = preparer._cache_key(source, _config())
    assert key is not None
    lock = preparer._conform_lock(key)

    def fake_three_second_warm() -> None:
        with lock:
            time.sleep(3.0)

    warm_thread = threading.Thread(target=fake_three_second_warm, daemon=True)
    warm_thread.start()
    time.sleep(0.1)

    started = time.monotonic()
    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    seg = report.source_plan.segments[0]
    # Fallback shape: a plain per-plan file, NOT a pointer into the (contended)
    # cache object -- this one airing gets its own bounded conform instead.
    assert "conform-cache" not in seg.path
    assert Path(seg.path).is_file()


def test_schedule_warm_job_skips_when_cache_already_populated_before_it_runs(
    tmp_path: Path,
) -> None:
    """Item 66 round-4 (Opus review, point 1): a queued warm job may sit in
    the single-worker warm queue for a while (round-3, point 4) -- by the
    time it is about to run, the identical asset may already be cached
    (e.g. a foreground untrimmed-miss conform promoted its own
    already-finished file straight in). The job must re-check {key}.ts
    existence (and a fresh meta) right before doing any work and skip the
    redundant re-conform if it's already there.

    Item 66 round-8 (MEDIUM fix): "already cached" now requires the
    simulated meta to genuinely carry ``full_asset_conform=True`` -- a
    flagless meta (what this test used to simulate) is no longer treated
    as a real cache hit by the job's own skip check (see
    ``_write_cache_meta``'s and the cache-HIT check's docstrings for why),
    so the simulated write below must match what a genuine promotion
    (``_promote_conform_into_cache``) actually persists."""
    calls: list[list[str]] = []
    warm_jobs: list = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    loudness = _loudness()

    preparer._schedule_warm(key, source, config, loudness, False)
    assert len(warm_jobs) == 1

    # Simulate the cache being populated by a concurrent caller WHILE this
    # job was still sitting in the queue.
    preparer._write_cache_meta(key, loudness, False, full_asset_conform=True)
    cache_dir = tmp_path / "work" / "conform-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.ts").write_text("already cached", encoding="utf-8")

    warm_jobs[0]()  # run the queued job now

    assert calls == []  # skipped the redundant re-conform


def test_cache_disabled_by_nonpositive_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0")
    calls: list[list[str]] = []
    probes: list[dict] = []
    for _ in range(2):
        SourcePreparer(
            work_dir=tmp_path / "work",
            ffmpeg_runner=_counting_runner(calls),
            loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(),
            warm_scheduler=lambda job: job(),
            playout_trim_supported=True,
        ).prepare(_untrimmed_plan(tmp_path), _config())

    assert len(calls) == 2  # conforms every airing — the historic behavior
    assert not (tmp_path / "work" / "conform-cache").exists()


@pytest.mark.parametrize("inpoint", [None, 0.0])
def test_cache_hit_stream_copies_when_engine_cannot_trim(
    tmp_path: Path, inpoint: float | None
) -> None:
    """The GStreamer engine reads only segment.path (no inpoint/outpoint), so
    under playout_trim_supported=False a cache hit must emit a TRIM-FREE
    per-plan output produced by a fast `-c copy` window when starting at zero,
    and never a trimmed segment the engine would ignore."""

    calls: list[list[str]] = []
    common: dict = {
        "work_dir": tmp_path / "work",
        "loudness_checker": lambda **_kwargs: _loudness(),
        "warm_scheduler": lambda job: job(),
    }
    plan = _untrimmed_plan(tmp_path)
    plan.segments[0].inpoint_seconds = inpoint
    # Warm the cache via a first airing (flag value irrelevant for warming).
    SourcePreparer(
        ffmpeg_runner=_counting_runner([]), playout_trim_supported=False, **common
    ).prepare(plan, _config())

    report = SourcePreparer(
        ffmpeg_runner=_counting_runner(calls), playout_trim_supported=False, **common
    ).prepare(plan, _config())

    assert len(calls) == 1
    copy_args = calls[0]
    assert copy_args[copy_args.index("-c") : copy_args.index("-c") + 2] == ["-c", "copy"]
    assert "-b:v" not in copy_args  # no re-encode
    seg = report.source_plan.segments[0]
    assert seg.inpoint_seconds is None and seg.outpoint_seconds is None  # trim-free contract
    assert "conform-cache" not in seg.path  # per-plan output, not the cache object


@pytest.mark.parametrize("inpoint", [1.0, 5.166378])
@pytest.mark.parametrize("normalize", [False, True])
def test_nonzero_cache_trim_decodes_original_when_engine_cannot_trim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inpoint: float, normalize: bool
) -> None:
    """A GOP-aligned cache copy can leave leading audio and starve A/V preroll."""
    monkeypatch.setattr(preparer_module, "probe_canonical_video", lambda *a, **kw: True)
    calls: list[list[str]] = []
    probes: list[dict] = []
    common: dict = {
        "work_dir": tmp_path / "work",
        "loudness_checker": lambda **kwargs: (
            probes.append(kwargs)
            or _loudness("too_loud" if normalize else "ok", -18.0 if normalize else -24.1)
        ),
        "warm_scheduler": lambda job: job(),
        "window_leveler": _no_ride,
    }
    plan = _source_plan(tmp_path)
    source = Path(plan.segments[0].path)
    # Create a genuine full-asset cache entry through the existing warm path.
    SourcePreparer(
        ffmpeg_runner=_counting_runner([]), playout_trim_supported=True, **common
    ).prepare(plan, _config())
    plan.segments[0].inpoint_seconds = inpoint
    plan.segments[0].outpoint_seconds = inpoint + 12.5

    report = SourcePreparer(
        ffmpeg_runner=_counting_runner(calls), playout_trim_supported=False, **common
    ).prepare(plan, _config())

    assert len(probes) == 1  # reuse the original-source loudness measurement
    assert calls
    assert all(args[args.index("-i") + 1] == str(source) for args in calls)
    conform_args = calls[-1]
    assert conform_args[conform_args.index("-ss") + 1] == f"{inpoint:g}"
    assert conform_args[conform_args.index("-t") + 1] == "12.5"
    assert conform_args[conform_args.index("-c:v") + 1] == _config().canonical_profile.video_codec
    assert "-c" not in conform_args
    assert ("-af" in conform_args) is normalize
    seg = report.source_plan.segments[0]
    assert seg.inpoint_seconds is None and seg.outpoint_seconds is None
    assert "conform-cache" not in seg.path


# ---------------------------------------------------------------------------
# Item 66 — first ON_AIR no longer waits behind a whole-clip conform on the
# synchronous start path. Revised after Opus review of PR #180: the original
# single-threaded (`-threads 1`) foreground conform and unconditional
# untrimmed-MISS-into-cache promotion both measured badly on real hardware
# (HALO) and are replaced below by a thread cap, per-asset loudness
# memoization, foreground-conform-promotes-into-cache, and a single-worker
# warm queue.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threads", [1, 3])
@pytest.mark.parametrize("copy_video", [False, True])
def test_build_conform_source_args_threads_param(
    tmp_path: Path, threads: int, copy_video: bool
) -> None:
    """``build_conform_source_args``'s ``threads`` argument (replacing the
    old boolean ``background`` flag) caps decoder, filters, and encoder only
    when given, including when video is copied and audio is encoded."""
    source = tmp_path / "source.mp4"
    output = tmp_path / "prepared.ts"
    profile = _config().canonical_profile

    default_args = build_conform_source_args(
        source_path=source,
        output_path=output,
        segment=None,
        profile=profile,
        copy_video=copy_video,
    )
    for option in ("-threads", "-filter_threads", "-filter_complex_threads"):
        assert option not in default_args

    capped_args = build_conform_source_args(
        source_path=source,
        output_path=output,
        segment=None,
        profile=profile,
        threads=threads,
        copy_video=copy_video,
    )
    input_index = capped_args.index("-i")
    codec_limits = [i for i, value in enumerate(capped_args) if value == "-threads"]
    assert len(codec_limits) == 2
    assert codec_limits[0] < input_index < codec_limits[1]
    assert all(capped_args[index + 1] == str(threads) for index in codec_limits)
    for option in ("-filter_threads", "-filter_complex_threads"):
        assert capped_args.count(option) == 1
        assert capped_args[capped_args.index(option) + 1] == str(threads)


def test_foreground_thread_cap_is_pinned_to_cpu_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 66 round-3 (Opus review, point 2): pin the exact cap formula --
    half the box's cores, floored at 1 -- with a monkeypatched
    ``os.cpu_count()``, mirroring the shape of
    ``tests/captions/test_caption_tap_worker.py``'s
    ``test_default_concurrency_is_one_channel_per_eight_cpus``, including
    the ``cpu_count() -> None`` case (documented as possible by the stdlib)."""
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: 8)
    assert preparer_module._foreground_thread_cap() == 4
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: 4)
    assert preparer_module._foreground_thread_cap() == 2
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: 2)
    assert preparer_module._foreground_thread_cap() == 1
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: 1)
    assert preparer_module._foreground_thread_cap() == 1
    # `os.cpu_count()` is documented as possibly None.
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: None)
    assert preparer_module._foreground_thread_cap() == 1


def test_conform_full_asset_into_cache_thread_param(tmp_path: Path) -> None:
    """``_conform_full_asset_into_cache``'s ``threads`` parameter must reach
    ``build_conform_source_args``: the warm-behind path (default, ``threads=1``)
    still caps the encode at one thread so a background warm can never starve
    the on-air encoder, but an explicit foreground cap must be honored
    verbatim for the synchronous caller (point 2, Opus review: the prior
    unconditional ``background=False`` -- fully unthrottled -- measured
    233s/300s at `-threads 1` vs 36.6s/300s unthrottled on HALO, an
    unacceptable regression on the synchronous content-reload path)."""
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    loudness = _loudness()

    key_bg = preparer._cache_key(source, config)
    assert key_bg is not None
    preparer._conform_full_asset_into_cache(key_bg, source, config, loudness, False)
    warm_args = calls[-1]
    assert warm_args[warm_args.index("-threads") : warm_args.index("-threads") + 2] == [
        "-threads",
        "1",
    ]

    # A different source (different cache key) so the second call is not
    # short-circuited by the first call's cache entry -- exercise an
    # explicit foreground cap.
    source2 = tmp_path / "long-recording-2.mp4"
    source2.write_text("different fake long media", encoding="utf-8")
    key_fg = preparer._cache_key(source2, config)
    assert key_fg is not None
    calls.clear()
    preparer._conform_full_asset_into_cache(key_fg, source2, config, loudness, False, threads=4)
    fg_args = calls[-1]
    assert fg_args[fg_args.index("-threads") : fg_args.index("-threads") + 2] == [
        "-threads",
        "4",
    ]


def test_untrimmed_miss_runs_one_bounded_conform_and_links_into_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-3 BLOCKER fix (Opus review, point 1): with the
    GStreamer engine (``playout_trim_supported=False``, the constructor
    default) an untrimmed cache MISS falls through to a bounded
    per-segment conform (``-t <duration>``, thread-capped). The round-2
    version of this fix moved that conform's output INTO the cache and
    then copied it back OUT for the per-plan file -- an extra full-length
    copy on the blocking path. The per-plan file must now be the DIRECT
    result of the one ffmpeg call (finished before the cache is touched at
    all), and the cache must be populated from a link/copy of that same
    file, not a second ffmpeg invocation.

    Item 66 round-8 (HIGH fix): the direct-promotion path this test targets
    now requires a KNOWN ``media_duration`` that demonstrably covers the
    segment (see ``is_full_asset_conform`` in ``preparer.py`` -- an unknown
    duration always routes to ``_schedule_warm`` instead, since it can no
    longer be trusted that an untrimmed segment reaching here is genuinely
    the whole asset). ``probe_media_duration_seconds`` is patched to return
    exactly the plan's own ``duration_seconds`` (3600s) so this test keeps
    exercising the direct-link path it was written for, rather than the
    fake ``.mp4`` (plain text, no real media) failing a real ffprobe call
    and silently falling into the round-8 fail-closed branch instead."""
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda *_a, **_k: 3600.0)
    calls: list[list[str]] = []
    warm_jobs: list = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
        # playout_trim_supported left at its default (False) -- the
        # GStreamer-engine wiring this fix targets.
    )

    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert len(calls) == 1  # exactly one ffmpeg call -- no copy-out round trip
    conform_args = calls[0]
    assert conform_args[conform_args.index("-t") : conform_args.index("-t") + 2] == [
        "-t",
        "3600",
    ]
    assert conform_args[conform_args.index("-threads") : conform_args.index("-threads") + 2] == [
        "-threads",
        "4",  # max(1, 8 // 2)
    ]
    assert "conform-cache" not in conform_args[-1]  # conforms to a private per-plan tmp
    seg = report.source_plan.segments[0]
    assert "conform-cache" not in seg.path  # the re-encoded per-plan file itself airs
    assert Path(seg.path).is_file()  # what airs must actually exist
    assert len(warm_jobs) == 0  # no redundant warm -- this conform already populated the cache

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    cache_ts = tmp_path / "work" / "conform-cache" / f"{key}.ts"
    assert cache_ts.is_file()
    assert (tmp_path / "work" / "conform-cache" / f"{key}.json").is_file()
    assert not cache_ts.with_suffix(".ts.tmp").exists()  # no leftover tmp sibling
    # Linked (or copied), never MOVED: the per-plan file must still exist too.
    assert Path(seg.path).exists()

    # A second prepare() of the same asset is now a genuine cache HIT: the
    # engine still can't trim at playout, so it costs one fast `-c copy`
    # copy-out (never a re-encode) and zero warms.
    calls.clear()
    warm_jobs.clear()
    preparer.prepare(_untrimmed_plan(tmp_path), _config())
    assert len(calls) == 1
    hit_copy_args = calls[0]
    assert hit_copy_args[hit_copy_args.index("-c") : hit_copy_args.index("-c") + 2] == [
        "-c",
        "copy",
    ]
    assert "-b:v" not in hit_copy_args
    assert warm_jobs == []


def test_promote_finished_conform_links_without_moving_the_per_plan_file(
    tmp_path: Path,
) -> None:
    """Unit-level companion: ``_promote_finished_conform_into_cache`` must
    populate the cache from ``finished_output_path`` without ever making
    that path disappear (a link or a copy, never a move)."""
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    finished = tmp_path / "already-airing.ts"
    finished.write_text("prepared bytes", encoding="utf-8")

    preparer._promote_finished_conform_into_cache(key, finished, source, _loudness(), False)

    cache_ts = tmp_path / "work" / "conform-cache" / f"{key}.ts"
    assert cache_ts.is_file()
    assert cache_ts.read_text(encoding="utf-8") == "prepared bytes"
    assert finished.is_file()  # never moved
    assert finished.read_text(encoding="utf-8") == "prepared bytes"


def test_promote_finished_conform_queues_a_copy_job_when_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-4 (Opus review, point 3): ``os.link`` can fail (e.g.
    the cache lives on a different volume than the per-plan directory) --
    the fallback is no longer an inline ``shutil.copy2`` on the
    synchronous start path (a full byte-for-byte copy of a long asset is
    exactly the kind of blocking work item 66 exists to close). It must
    instead be QUEUED onto the warm scheduler and return immediately
    without having copied anything yet."""
    warm_jobs: list = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    finished = tmp_path / "already-airing.ts"
    finished.write_text("prepared bytes", encoding="utf-8")

    def _raising_link(_src: object, _dst: object) -> None:
        raise OSError("simulated cross-volume link failure")

    monkeypatch.setattr(preparer_module.os, "link", _raising_link)

    preparer._promote_finished_conform_into_cache(key, finished, source, _loudness(), False)

    cache_ts = tmp_path / "work" / "conform-cache" / f"{key}.ts"
    assert not cache_ts.exists()  # not populated yet -- the copy is only queued
    assert len(warm_jobs) == 1

    warm_jobs[0]()  # run the queued copy job

    assert cache_ts.is_file()  # now populated via the background shutil.copy2
    assert cache_ts.read_text(encoding="utf-8") == "prepared bytes"
    assert finished.is_file()


def test_cache_copy_promotion_job_cleans_up_tmp_on_a_post_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-6 (Opus review, point 5): the copy job's INNER
    try/except already cleaned up a partial ``.ts.tmp`` when ``shutil.
    copy2`` itself failed -- but a failure AFTER a successful copy (e.g.
    ``_promote_conform_into_cache``'s own over-budget raise, which runs
    after the copy2 succeeded but before the tmp is renamed away) hit only
    the OUTER except, which did not clean up. It must now unlink the tmp
    there too."""
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0.000000001")  # ~1 byte -- guarantees
    # _promote_conform_into_cache's own over-budget SourcePrepareError, raised AFTER copy2
    # succeeds and BEFORE the tmp is renamed away.
    warm_jobs: list = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    finished = tmp_path / "already-airing.ts"
    finished.write_text("prepared bytes long enough to exceed a 1-byte budget", encoding="utf-8")

    def _raising_link(_src: object, _dst: object) -> None:
        raise OSError("simulated cross-volume link failure")

    monkeypatch.setattr(preparer_module.os, "link", _raising_link)

    preparer._promote_finished_conform_into_cache(key, finished, source, _loudness(), False)
    assert len(warm_jobs) == 1
    warm_jobs[0]()  # must not raise -- the job's own outer except must swallow it

    tmp = tmp_path / "work" / "conform-cache" / f"{key}.ts.tmp"
    assert not tmp.exists()  # cleaned up by the outer except, not left for the orphan reap
    assert finished.is_file()  # the segment's own file is never touched by any of this


def test_promote_finished_conform_does_not_deadlock_with_synchronous_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-5 BLOCKER fix (Opus review, point 1): round-4 scheduled
    the cross-volume copy job from INSIDE the still-held per-key lock, and
    the job itself blocking-acquired that SAME lock -- with a SYNCHRONOUS
    warm_scheduler (``lambda job: job()``, the exact shape
    ``test_aired_before_asset_prepares_with_zero_ffmpeg_work`` at line 622
    uses), the job runs inline, before this frame's own ``finally:
    lock.release()`` could ever execute -- a guaranteed self-deadlock. This
    call must simply RETURN."""
    warm_jobs: list = []

    def synchronous_scheduler(job: Callable[[], None]) -> None:
        warm_jobs.append(job)
        job()  # run it inline, immediately -- the exact shape that self-deadlocked in round 4

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=synchronous_scheduler,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    finished = tmp_path / "already-airing.ts"
    finished.write_text("prepared bytes", encoding="utf-8")

    def _raising_link(_src: object, _dst: object) -> None:
        raise OSError("simulated cross-volume link failure")

    monkeypatch.setattr(preparer_module.os, "link", _raising_link)

    # Must return promptly -- pytest's own timeout is the real backstop,
    # but the point of this test is that this call does not hang at all.
    preparer._promote_finished_conform_into_cache(key, finished, source, _loudness(), False)

    assert len(warm_jobs) == 1  # the copy job ran synchronously, inline, without deadlocking
    cache_ts = tmp_path / "work" / "conform-cache" / f"{key}.ts"
    assert cache_ts.is_file()  # and it actually completed the copy + promotion
    assert cache_ts.read_text(encoding="utf-8") == "prepared bytes"


def test_promote_finished_conform_swallows_over_budget_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER regression guard: a promotion failure (here, this single
    entry alone exceeding ``CIVICCAST_CONFORM_CACHE_GB``) must never
    propagate -- the per-plan file already airs and must keep existing."""
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0.000000001")  # ~1 byte
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None  # a positive (if tiny) budget still enables caching
    finished = tmp_path / "already-airing.ts"
    finished.write_text("prepared bytes long enough to exceed a 1-byte budget", encoding="utf-8")

    preparer._promote_finished_conform_into_cache(
        key, finished, source, _loudness(), False
    )  # must not raise

    assert finished.is_file()  # the per-plan file survives regardless
    assert not (tmp_path / "work" / "conform-cache" / f"{key}.ts").exists()  # evicted over budget


def test_untrimmed_miss_still_airs_when_cache_promotion_fails_over_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end companion: the exact BLOCKER scenario through ``prepare()``
    itself -- a promotion that fails over budget must not turn into a
    failed ``prepare()`` call. Before this fix, ``_promote_conform_into_
    cache``'s ``SourcePrepareError`` propagated straight out of
    ``_prepare_segment`` because the per-plan file had already been moved
    into the (now-evicted) cache location."""
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0.000000001")  # ~1 byte
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )

    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())  # must not raise

    assert len(calls) == 1
    seg = report.source_plan.segments[0]
    assert Path(seg.path).is_file()  # the segment airs regardless of the cache-promotion failure


def test_untrimmed_miss_still_conforms_full_asset_when_engine_can_trim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to the test above: with ``playout_trim_supported=True`` (the
    legacy ffmpeg-concat engine) the SHAPE of an untrimmed miss is unchanged
    by item 66 -- it still conforms the whole asset synchronously straight
    into the cache (no ``-t``/``-ss``), matching the pre-existing
    ``test_aired_before_asset_prepares_with_zero_ffmpeg_work`` contract.
    What DOES change (point 2, Opus review): this synchronous conform is now
    thread-capped rather than fully unthrottled, since it is reachable
    outside first-ON_AIR too (``EgressDaemon._try_content_reload``'s
    synchronous prepare while another channel may be on air) -- pin the
    exact cap value, not just its presence."""
    monkeypatch.setattr(preparer_module.os, "cpu_count", lambda: 4)
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
        playout_trim_supported=True,
    )

    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert len(calls) == 1
    args = calls[0]
    assert "-t" not in args  # whole-asset conform, not bounded to the segment
    assert "-ss" not in args
    assert args[args.index("-threads") : args.index("-threads") + 2] == [
        "-threads",
        "2",  # max(1, 4 // 2) -- point 2: exact cap, no longer fully unthrottled
    ]
    seg = report.source_plan.segments[0]
    assert "conform-cache" in seg.path  # emitted straight from the cache object


def test_loudness_probe_is_bounded_to_the_segment_window_when_trimmed(tmp_path: Path) -> None:
    """Item 66 round-3, point 7: a trimmed segment's loudness probe must be
    bounded to its own window (``-ss <inpoint>``, ``-t <duration>``), not
    the whole file."""
    probes: list[dict] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: None,
    )

    # _source_plan: inpoint=2, outpoint=14.5, duration=12.5 -- trimmed.
    preparer.prepare(_source_plan(tmp_path), _config())

    assert len(probes) == 1
    assert probes[0]["probe_start_seconds"] == 2
    assert probes[0]["probe_duration_seconds"] == 12.5


def test_untrimmed_probe_samples_mid_file_not_the_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-4 (Opus review, point 2): an untrimmed probe samples
    40% into the asset's REAL MEDIA DURATION for 120s, NOT the head. A
    round-3 HEAD sample can land on cold-open silence/room tone and
    measure the silence floor instead of the program's real loudness -- a
    real field failure (-70 LUFS at the head of a 39-minute meeting
    recording) that would then drive loudnorm's normalization target
    completely wrong. Round-5 (Opus review, point 2) corrected WHICH
    duration is sampled from: ``segment.duration_seconds`` is the
    schedule-slot-capped duration (source_plan.py's ``_segment_duration``),
    not the asset's own media duration, so this test monkeypatches
    ``probe_media_duration_seconds`` to a known value rather than relying
    on the segment's duration field."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 3600.0)
    probes: list[dict] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())  # 3600s asset

    assert len(probes) == 1
    assert probes[0]["probe_start_seconds"] == 1440.0  # 40% of the 3600s media duration
    assert probes[0]["probe_duration_seconds"] == 120.0

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["media_duration_seconds"] == 3600.0  # cached for a later prepare() call


@pytest.mark.parametrize(
    ("duration", "expected_start", "expected_duration", "second_sample_possible"),
    [
        # Item 66 round-7 (Opus review, point 2, MEDIUM fix): round-6 used
        # 240s (_UNTRIMMED_LOUDNESS_PROBE_CAP_S * 2) as the single-sample
        # cutoff, which let a 120-240s asset's HEAD-only 120s sample stand
        # in for the whole file when it covered at most HALF of it -- a real
        # field failure (a 200s clip whose only audio starts at 130s, past
        # the sampled [0, 120) window, got memoized as silent). The cutoff
        # is now the probe window's own size (120s): at or below it, one
        # sample spanning the whole file is taken from the very start; above
        # it, the "long asset" branch is used instead -- its first sample
        # sits at 40% in (never the head), and a genuinely non-overlapping
        # second sample (start >= first start + 120s, verified below) is
        # only attempted when there is room for one.
        (67.0, 0.0, 67.0, False),  # the exact duration from the PROVEN field failure
        (120.0, 0.0, 120.0, False),  # exactly the new single/dual boundary
        # 150s and 240s: now in the "long asset" branch (>120s), but neither
        # has room for a genuinely non-overlapping second 120s window (that
        # needs >= 240s) -- no resample is attempted, so the ONE (40%-in,
        # never head) sample is what's used. This is exactly what fixes the
        # 200s field failure above: 150s's window [30, 150) and 240s's
        # window [96, 216) both reach well past the old HEAD-only [0, 120)
        # sample and would have caught audio starting at 130s.
        (150.0, 30.0, 120.0, False),
        (240.0, 96.0, 120.0, False),
        (400.0, 160.0, 120.0, True),  # smallest duration where two 120s windows just fit
        (3600.0, 1440.0, 120.0, True),
    ],
)
def test_untrimmed_probe_window_by_media_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
    expected_start: float,
    expected_duration: float,
    second_sample_possible: bool,
) -> None:
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: duration)
    probes: list[dict] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        # A floor reading throughout: proves whether a resample is even
        # ATTEMPTED, regardless of whether it would end up trusted.
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(measured_lufs=-70.0),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert probes[0]["probe_start_seconds"] == expected_start
    assert probes[0]["probe_duration_seconds"] == expected_duration
    if second_sample_possible:
        assert len(probes) == 2  # the floor reading triggers exactly one resample
        # The two windows must never overlap: second start >= first start + 120s.
        assert probes[1]["probe_start_seconds"] >= expected_start + 120.0
        assert probes[1]["probe_start_seconds"] + probes[1]["probe_duration_seconds"] <= duration
    else:
        assert len(probes) == 1  # no second window exists to sample -- none attempted


def test_untrimmed_probe_trusts_a_single_conclusive_sample_as_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to the parametrized test above: for a short, KNOWN-duration
    asset (<=120s -- item 66 round-7 lowered this from 240s, see
    ``_SHORT_ASSET_SINGLE_SAMPLE_MAX_S``'s docstring) whose one sample
    already covers the whole file, a floor reading is trusted directly as
    genuine silence -- there is no second window to corroborate against,
    and none is needed."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 90.0)
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(measured_lufs=-70.0),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["measured_lufs"] == -70.0
    assert meta["normalized"] is False  # trusted as silent from the one sample alone


def test_untrimmed_probe_at_120_to_240s_never_trusts_an_unusable_resample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-7 (point 2, MEDIUM fix): a 150s asset (in the "long
    asset" branch now that the single-sample cutoff is 120s, not 240s) has
    no room for a genuinely non-overlapping second 120s window -- two full
    120s windows need >= 240s. The floor reading on the one available
    (40%-in) sample is used directly as the measurement: it is NEITHER
    trusted as conclusive silence (only the <=120s branch does that) NOR
    corroborated by a resample (none was attempted, since none would be
    genuinely independent) -- exactly the round-6 "never trust an
    overlapping/identical resample as evidence" invariant, now also
    enforced in the range round-6 itself couldn't reach (120-240s)."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 150.0)
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        # status="failed": a real loudness checker reports this for a -70.0
        # reading against a -24.0 target -- the test double for the
        # conclusive-silence test above defaults to "ok" only because
        # ``silent_asset`` there forces ``normalized`` to False regardless.
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-70.0),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["measured_lufs"] == -70.0
    # NOT marked "normalized is False" (never trusted as conclusive silence,
    # unlike the <=120s case) -- the floor reading is used as a real
    # measurement, so normalization proceeds toward the target exactly as it
    # would for any other out-of-tolerance reading.
    assert meta["normalized"] is True


def test_untrimmed_probe_falls_back_to_a_second_bounded_sample_on_silence_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-5 (Opus review, point 3): when the mid-file sample
    measures at/below the silence floor (-60 LUFS), resample at a
    DIFFERENT bounded offset (70% into the media, still 120s -- never a
    whole-file decode, which measured 46.7s on a 39-minute clip) and use
    that reading -- the floor sample itself is never used or cached."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 3600.0)
    probes: list[dict] = []

    def loudness_checker(**kwargs: object) -> LoudnessGateResult:
        probes.append(kwargs)
        if kwargs.get("probe_start_seconds") == 1440.0:
            return _loudness(measured_lufs=-70.0)  # the 40%-in sample: silence
        # the 70%-in resample: a real reading, outside tolerance -- needs normalizing
        return _loudness(status="failed", measured_lufs=-16.0)

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=loudness_checker,
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert len(probes) == 2  # the first bounded sample, then exactly one resample -- never a loop
    assert probes[0]["probe_start_seconds"] == 1440.0  # 40% of 3600s
    assert probes[0]["probe_duration_seconds"] == 120.0
    assert probes[1]["probe_start_seconds"] == 2520.0  # 70% of 3600s -- a DIFFERENT offset
    assert probes[1]["probe_duration_seconds"] == 120.0  # still bounded, never None (whole file)

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["measured_lufs"] == -16.0  # the resample's reading, never the floor sample
    assert (
        meta["normalized"] is True
    )  # -16.0 is outside -24.0 +/- 1.0 tolerance -- still normalizes


def test_untrimmed_probe_keeps_first_reading_when_resample_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-6 (Opus review, point 4): if the resample itself fails
    to produce a measurement (e.g. an ffmpeg error), the first (floor)
    reading is kept -- never raise from a failed resample, and never
    silently overwrite a usable reading with an unmeasured one."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 3600.0)

    def loudness_checker(**kwargs: object) -> LoudnessGateResult:
        if kwargs.get("probe_start_seconds") == 1440.0:
            # the 40%-in sample: silence, well outside tolerance of the -24 target.
            return _loudness(status="failed", measured_lufs=-70.0)
        # the 70%-in resample fails outright -- no measurement at all.
        return _loudness(status="failed", measured_lufs=None)

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=loudness_checker,
        warm_scheduler=lambda job: None,
    )

    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())  # must not raise

    seg = report.source_plan.segments[0]
    assert Path(seg.path).is_file()
    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["measured_lufs"] == -70.0  # the first (floor) reading, kept
    assert meta["normalized"] is True  # never marked silent -- the resample couldn't confirm it


def test_untrimmed_probe_treats_asset_as_silent_when_both_samples_hit_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion: TWO independent samples (different offsets) both at the
    silence floor means the asset really is silent -- normalize=False is
    cached rather than trusting either floor reading as a real loudnorm
    target."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 3600.0)
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(measured_lufs=-70.0),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["measured_lufs"] == -70.0
    assert meta["normalized"] is False  # never normalize toward a target that isn't there


def test_untrimmed_probe_does_not_fall_back_when_sample_is_above_the_floor(
    tmp_path: Path,
) -> None:
    """Companion: a normal (non-floor) mid-file sample is used as-is -- no
    fallback probe runs at all."""
    probes: list[dict] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(measured_lufs=-24.1),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert len(probes) == 1  # no fallback triggered


def test_untrimmed_probe_with_unknown_duration_samples_from_zero_never_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-6 BLOCKER-adjacent fix (Opus review, point 1): PROVEN
    on a real 67-second clip (real loudness -10.9 LUFS): when the real
    media duration is unknown (ffprobe unavailable/failed), round 5's
    fixed 120s/240s offsets landed entirely past the clip's actual end for
    ANY asset shorter than 120s, read as silence, and got misreported as a
    genuinely silent asset. With duration unknown, the probe must sample
    from 0s instead (never past EOF for any asset with real audio), and a
    floor reading there must NEVER be trusted as proof of silence (there
    is no independent second window, and no known length, to corroborate
    it against)."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: None)
    probes: list[dict] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        # Simulates the PROVEN failure mode: a blind offset lands past EOF
        # and reads as silence, even though the real asset is not silent.
        loudness_checker=lambda **kwargs: (
            probes.append(kwargs) or _loudness(status="failed", measured_lufs=-70.0)
        ),
        warm_scheduler=lambda job: None,
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert len(probes) == 1  # sampled once, from the start -- never a second/corroborating sample
    assert probes[0]["probe_start_seconds"] == 0.0
    assert probes[0]["probe_duration_seconds"] == 120.0

    key = preparer._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["measured_lufs"] == -70.0
    assert meta["normalized"] is True  # NEVER trusted as silent when duration is unknown
    assert meta["media_duration_seconds"] is None


def test_cache_hit_utime_race_falls_through_to_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-3, point 4: ``os.utime(cached_ts)`` on a cache HIT can
    race a concurrent eviction pass that removed the entry between the
    ``.is_file()`` check and this call -- must fall through to a MISS
    (re-conform) instead of an unguarded ``FileNotFoundError`` crash."""
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
        playout_trim_supported=True,
    )
    preparer.prepare(_untrimmed_plan(tmp_path), _config())  # warm the cache: a real HIT exists
    assert len(calls) == 1
    calls.clear()

    def _raising_utime(_path: object, *_a: object, **_k: object) -> None:
        raise FileNotFoundError(_path)

    monkeypatch.setattr(preparer_module.os, "utime", _raising_utime)

    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())  # must not raise

    assert len(calls) == 1  # treated as a miss -- re-conformed
    seg = report.source_plan.segments[0]
    assert "conform-cache" in seg.path


def test_evict_cache_over_budget_reaps_orphaned_tmp_and_meta(tmp_path: Path) -> None:
    """Item 66 round-3, point 3: an abandoned ``.ts.tmp`` older than 1h is
    reaped; a younger one is left alone. A ``.json`` with no sibling
    ``.ts`` older than 24h is reaped; one with a sibling ``.ts`` is kept
    regardless of age."""
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    cache_dir = tmp_path / "work" / "conform-cache"
    cache_dir.mkdir(parents=True)

    old_tmp = cache_dir / "orphan1.ts.tmp"
    old_tmp.write_text("stale", encoding="utf-8")
    young_tmp = cache_dir / "orphan2.ts.tmp"
    young_tmp.write_text("still writing", encoding="utf-8")
    orphan_meta = cache_dir / "orphan3.json"
    orphan_meta.write_text("{}", encoding="utf-8")
    paired_meta = cache_dir / "orphan4.json"
    paired_meta.write_text("{}", encoding="utf-8")
    paired_ts = cache_dir / "orphan4.ts"
    paired_ts.write_text("real cache entry", encoding="utf-8")

    old_tmp_time = time.time() - preparer_module._ORPHAN_CACHE_SCRATCH_MAX_AGE_S - 60
    os.utime(old_tmp, (old_tmp_time, old_tmp_time))
    old_meta_time = time.time() - preparer_module._ORPHAN_CACHE_META_MAX_AGE_S - 60
    os.utime(orphan_meta, (old_meta_time, old_meta_time))

    preparer._evict_cache_over_budget()

    assert not old_tmp.exists()  # reaped: old orphaned .tmp
    assert young_tmp.exists()  # kept: still within the age floor
    assert not orphan_meta.exists()  # reaped: old .json with no sibling .ts
    assert paired_meta.exists()  # kept: has a sibling .ts
    assert paired_ts.exists()


def test_evict_cache_over_budget_counts_live_tmp_bytes_toward_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-3, point 3: a live (young) ``.ts.tmp`` counts toward
    the budget even though it is never itself evicted here -- so a real
    ``.ts`` entry is evicted earlier to make room for in-flight writes."""
    monkeypatch.setenv("CIVICCAST_CONFORM_CACHE_GB", "0.0000001")  # 100 bytes
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    cache_dir = tmp_path / "work" / "conform-cache"
    cache_dir.mkdir(parents=True)
    ts_entry = cache_dir / f"{'a' * 32}.ts"
    ts_entry.write_text("x" * 60, encoding="utf-8")
    live_tmp = cache_dir / "bbbb.ts.tmp"
    live_tmp.write_text("y" * 60, encoding="utf-8")  # young -- not orphaned

    preparer._evict_cache_over_budget()

    # 60 (live .tmp, counted but never evicted here) + 60 (.ts) = 120 bytes,
    # over the ~100-byte budget -- the .ts entry is evicted to bring the
    # total back down; the live .tmp itself is left alone.
    assert not ts_entry.exists()
    assert live_tmp.exists()


def test_write_cache_meta_is_atomic_tmp_replace(tmp_path: Path) -> None:
    """Item 66 round-3, point 4: ``_write_cache_meta``'s sidecar write must
    be tmp+replace, matching every ``.ts`` write in this module -- no
    leftover ``.tmp`` sibling once the write completes."""
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )

    preparer._write_cache_meta("somekey", _loudness(), False)

    cache_dir = tmp_path / "work" / "conform-cache"
    assert (cache_dir / "somekey.json").is_file()
    assert not (cache_dir / "somekey.json.tmp").exists()


def test_cli_source_preparer_wires_playout_trim_supported(tmp_path: Path) -> None:
    """Item 66 round-3, point 6: ``cli.py``'s ``SourcePreparer(work_dir=
    work_dir)`` construction was missing ``playout_trim_supported`` entirely
    (silently defaulting to ``False`` even on the legacy ffmpeg-concat
    engine) -- it must now mirror ``automation.py``'s
    ``not gstreamer_engine_selected()`` wiring via the same helper."""
    import inspect

    from civiccast import cli as cli_module

    source = inspect.getsource(cli_module._run_egress_service)
    assert "gstreamer_engine_selected" in source
    assert "playout_trim_supported=not gstreamer_engine_selected()" in source


def test_loudness_probed_once_for_all_segments_of_one_asset_in_one_prepare(
    tmp_path: Path,
) -> None:
    """Item 66, point 1: 8 differently-trimmed segments of the SAME asset
    (same cache key, distinct (path, inpoint, outpoint) so prepare()'s own
    ``seen`` dedupe does not short-circuit them) used to mean 8 full-file
    loudness probes on the synchronous start path (~6.3 minutes measured).
    The first segment probes and persists the meta immediately -- before any
    conform for the asset exists -- so the other 7 reuse it."""
    probes: list[dict] = []
    calls: list[list[str]] = []
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **kwargs: probes.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: None,
    )

    def plan() -> EgressSourcePlan:
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label=f"segment-{i}",
                    path=str(source),
                    duration_seconds=30.0,
                    inpoint_seconds=float(i * 30),
                    outpoint_seconds=float(i * 30 + 30),
                )
                for i in range(8)
            ],
        )

    preparer.prepare(plan(), _config())

    assert len(probes) == 1  # exactly one loudness probe for all 8 segments
    assert len(calls) == 8  # each distinct trim still gets its own bounded conform


def test_loudness_probe_reused_across_prepares_of_the_same_asset(tmp_path: Path) -> None:
    """Companion: a SECOND prepare() of the same asset (a fresh
    ``SourcePreparer`` sharing ``work_dir``, no warm ever run) must run ZERO
    loudness probes -- the meta persisted by the first prepare() survives on
    disk and is read back before any probe is attempted."""
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")

    def plan() -> EgressSourcePlan:
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label="joined-late",
                    path=str(source),
                    duration_seconds=30.0,
                    inpoint_seconds=5.0,
                    outpoint_seconds=35.0,
                )
            ],
        )

    probes_1: list[dict] = []
    SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **kwargs: probes_1.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: None,  # the warm never runs -- no full conform is cached
    ).prepare(plan(), _config())
    assert len(probes_1) == 1

    probes_2: list[dict] = []
    SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **kwargs: probes_2.append(kwargs) or _loudness(),
        warm_scheduler=lambda job: None,
    ).prepare(plan(), _config())
    assert probes_2 == []  # reused from the meta the first prepare() persisted


def test_untrimmed_second_prepare_of_same_asset_runs_zero_ffprobes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-6 (Opus review, point 3): an untrimmed asset's real
    media duration (probed via ``probe_media_duration_seconds``) is cached
    in the same meta as its loudness result -- a SECOND ``prepare()`` of
    the same asset (a fresh ``SourcePreparer`` sharing ``work_dir``) must
    run ZERO ffprobes, same as it must run zero loudness probes. The
    duration read back from the reused meta (see ``_prepare_segment``'s
    ``if meta is not None:`` branch) must also survive the SECOND meta
    write that the untrimmed-miss tail makes (``_promote_finished_conform_
    into_cache`` -> ``_write_cache_meta`` again, without itself knowing the
    duration) -- proving the round-6 explicit-threading fix actually
    prevents the round-5 read-modify-write it replaced from being needed."""
    ffprobe_calls: list[Path] = []

    def counting_probe(path: Path) -> float | None:
        ffprobe_calls.append(path)
        return 3600.0

    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", counting_probe)

    preparer_1 = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    preparer_1.prepare(_untrimmed_plan(tmp_path), _config())
    assert len(ffprobe_calls) == 1

    key = preparer_1._cache_key(tmp_path / "long-recording.mp4", _config())
    assert key is not None
    meta_after_first = preparer_1._read_cache_meta(key)
    assert meta_after_first is not None
    assert meta_after_first["media_duration_seconds"] == 3600.0  # survives the conform-tail's
    # second _write_cache_meta call (the untrimmed-miss branch always writes meta twice: once
    # from the probe itself, again from _promote_finished_conform_into_cache's tail).

    ffprobe_calls.clear()
    preparer_2 = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    preparer_2.prepare(_untrimmed_plan(tmp_path), _config())

    assert ffprobe_calls == []  # zero ffprobes on the second prepare() of the same asset


def _slot_capped_untrimmed_plan(
    source: Path, *, duration_seconds: float, label: str = "segment"
) -> EgressSourcePlan:
    """An "untrimmed" (no inpoint/outpoint) segment whose own duration is
    SHORTER than the asset's real media length -- exactly D42's shape
    (`source_plan.py`'s `_segment_duration`, `min(slot, playable)`): a
    schedule slot shorter than the asset produces this without any
    inpoint/outpoint attached."""
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(label=label, path=str(source), duration_seconds=duration_seconds)
        ],
    )


def test_slot_capped_untrimmed_conform_is_not_promoted_as_full_asset_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-7 (point 1, HIGH BLOCKER fix). Reproduces the reviewer's
    scenario: D42 (`source_plan.py`'s `_segment_duration`, `min(slot,
    playable)`) makes a 30s schedule slot on a 67s untrimmed asset produce a
    30s bounded conform with NO inpoint/outpoint -- `trimmed` reads False
    even though this is only a fragment of the asset. Round-6 hard-linked
    that fragment into the persistent cache as if it were the whole asset
    (`_promote_finished_conform_into_cache`, unconditional on `not trimmed`
    alone); this must no longer happen -- the fragment must never become
    `{key}.ts`, and the real full-asset conform must be warmed behind it
    instead, exactly like a genuinely trimmed miss."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 67.0)
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    warm_jobs: list[Callable[[], None]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,  # never runs -- inspect state before any warm executes
    )

    report = preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=30.0), _config())

    # The segment still airs fine from its own per-plan file...
    seg = report.source_plan.segments[0]
    assert Path(seg.path).is_file()
    # ...but the 30s fragment must NOT have been hard-linked into the
    # persistent cache as if it were the whole 67s asset.
    key = preparer._cache_key(source, _config())
    assert key is not None
    cached_ts = preparer._cache_dir() / f"{key}.ts"
    assert not cached_ts.is_file()  # no fragment ever gets promoted as "the full asset"
    # A real full-asset warm was scheduled behind it instead (same fallback
    # a genuinely trimmed miss already used).
    assert len(warm_jobs) == 1


def test_slot_capped_untrimmed_conform_warms_the_true_full_asset(tmp_path: Path) -> None:
    """Companion to the test above, with a synchronous warm scheduler: once
    the warm-behind job actually runs, it conforms the WHOLE asset (no
    duration truncation -- ``_conform_full_asset_into_cache`` always builds
    with ``segment=None``) and marks the cache entry ``full_asset_conform``
    -- a later, longer-slot request for the same asset can then safely hit
    it (see the read-side test below)."""
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: job(),  # synchronous: runs inline
    )

    preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=30.0), _config())

    key = preparer._cache_key(source, _config())
    assert key is not None
    cached_ts = preparer._cache_dir() / f"{key}.ts"
    assert cached_ts.is_file()  # the warm populated the persistent cache
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta["full_asset_conform"] is True


def test_unknown_media_duration_on_untrimmed_slot_capped_segment_never_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-8 (HIGH fix), mock-level (no real ffmpeg needed):
    reproduces the round-7-vs-round-8 gap without depending on
    ffmpeg/ffprobe being on PATH (the real-ffmpeg suite in
    ``test_preparer_conform_cache_real_ffmpeg.py`` covers the same scenario
    end to end, but skips entirely when ffmpeg is missing, which is exactly
    why this fix had zero coverage on a runner without it).

    ``probe_media_duration_seconds`` is monkeypatched to return ``None`` --
    a genuinely unknown media duration, e.g. ffprobe unavailable/failing for
    this call -- for an untrimmed segment whose own ``duration_seconds`` is
    shorter than a real asset would be (D42's slot-cap shape). ``is_full_
    asset_conform`` must read False here: the bounded conform must fall
    through to ``_schedule_warm`` instead of being hard-linked into the
    cache as if it were the whole asset. Reverting the fix's ``media_
    duration is not None`` clause back to round-7's ``media_duration is
    None or ...`` shape makes this fail: with that clause removed, ``not
    trimmed`` alone is enough and the fragment gets promoted."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: None)
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    warm_jobs: list[Callable[[], None]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,  # never runs -- inspect state before any warm executes
    )

    report = preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=30.0), _config())

    # The segment still airs fine from its own per-plan file...
    seg = report.source_plan.segments[0]
    assert Path(seg.path).is_file()
    # ...but with the real duration unknown, nothing may be promoted as "the
    # full asset" from this unverified fragment.
    key = preparer._cache_key(source, _config())
    assert key is not None
    cached_ts = preparer._cache_dir() / f"{key}.ts"
    assert not cached_ts.is_file()  # no fragment ever gets promoted
    meta = preparer._read_cache_meta(key)
    assert meta is not None
    assert meta.get("full_asset_conform") is not True  # never marked as a genuine full conform
    # A real full-asset warm was scheduled behind it instead -- fail closed,
    # never trust the unverified fragment.
    assert len(warm_jobs) == 1


def test_full_asset_cache_hit_requires_the_explicit_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 66 round-7 (point 1, HIGH BLOCKER fix), read side. A meta file
    whose ``full_asset_conform`` is missing or False -- a legacy entry from
    before this fix, or (defense in depth) any future write that forgets to
    set it -- must never be trusted as the shared full-asset cache: a
    `segment.duration_seconds` vs `media_duration_seconds` numeric
    comparison alone cannot tell a genuinely full conform apart from a short
    one, because `media_duration_seconds` records the ASSET's true length,
    unaffected by whatever the `.ts` on disk actually holds. Plant a SHORT
    `.ts` with a meta that omits the flag (simulating the exact stale-entry
    shape the round-6 bug produced) and prove a later request re-conforms
    instead of trusting it."""
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 67.0)
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: None,
    )
    key = preparer._cache_key(source, _config())
    assert key is not None
    cache_dir = preparer._cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.ts").write_text("only 30s of real content", encoding="utf-8")

    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "loudness_status": "ok",
                "measured_lufs": -24.1,
                "normalized": False,
                "media_duration_seconds": 67.0,
                # no "full_asset_conform" key at all -- the pre-round-7 shape
            }
        ),
        encoding="utf-8",
    )

    report = preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=60.0), _config())

    assert len(calls) == 1  # re-conformed -- the stale entry was never trusted as a HIT
    seg = report.source_plan.segments[0]
    assert "conform-cache" not in seg.path  # its own bounded conform, not the stale cache object


def test_full_asset_cache_hit_trusted_once_flag_is_set(tmp_path: Path) -> None:
    """Positive companion: once a cache entry genuinely IS the whole asset
    (``full_asset_conform`` True), a later, shorter-slot request for the
    SAME asset correctly hits it -- the fix does not disable caching for the
    ordinary (correct) case, only for entries that were never proven full."""
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda job: job(),
        playout_trim_supported=True,  # simplest path to a genuine full-asset conform
    )
    # First airing: untrimmed MISS on an engine that trims at playout ->
    # _conform_full_asset_into_cache runs directly, always the whole file.
    preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=3600.0), _config())
    assert len(calls) == 1
    calls.clear()

    # A later, shorter-slot request for the same asset.
    report = preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=30.0), _config())

    assert calls == []  # zero ffmpeg work -- a genuine cache HIT
    seg = report.source_plan.segments[0]
    assert "conform-cache" in seg.path


def test_probe_only_write_cannot_clear_a_promoted_full_asset_conform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U65: a probe-only metadata write must never clear an existing
    full-asset conform's markers.

    The conform cache is shared by every channel and every prepare, and
    ``_prepare_segment`` reads ``meta`` ONCE per call, *before* its probe. Two
    prepares for the same asset therefore interleave like this:

        A: reads meta -> None (cache cold, so A takes the probe branch)
        B: finishes a whole-asset conform and PROMOTES it -- ``{key}.ts`` plus
           a sidecar carrying ``full_asset_conform=True``, method ``"ride"``
        A: finishes its probe and writes its own sidecar, which carries no
           markers

    That is the live 2026-09-29 sequence on the 4.4 h August 11 asset.
    ``_write_cache_meta`` REPLACES the whole sidecar, so A's write erases B's
    markers -- and the next prepare reads ``full_asset_conform`` False and
    re-conforms the entire asset (observed three times in one 8 h rung).

    The interleaving is reproduced deterministically in one thread: the racing
    prepare's own probe promotes the asset, so the probe-only write that
    follows it IS the live race -- no threads, no sleeps. The racing prepare
    is shaped so that its probe write is the LAST thing it does to this cache
    entry: it is UNTRIMMED (the probe block that persists at 2817 is the
    untrimmed one) on an engine that cannot trim at playout, so it conforms
    only a bounded window, and its slot is capped below the asset's real
    duration so the tail gate treats that window as a fragment (round-7) and
    warms instead of promoting. Its warm is not run. Nothing repairs the
    damage afterwards, so the final sidecar is attributable to the probe-only
    write alone.
    """
    monkeypatch.setattr(preparer_module, "probe_media_duration_seconds", lambda _path: 15949.2)
    source = tmp_path / "raw-source.mp4"
    source.write_text("fake media", encoding="utf-8")
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda _job: None,
        playout_trim_supported=True,
    )
    key = preparer._cache_key(source, _config())
    assert key is not None

    promoted: list[bool] = []

    def racing_loudness(**_kwargs: object) -> LoudnessGateResult:
        """B's promotion, landing while A's probe is in flight."""
        if not promoted:
            finished = tmp_path / "work" / "finished-full-asset.ts"
            finished.parent.mkdir(parents=True, exist_ok=True)
            finished.write_text("the whole asset", encoding="utf-8")
            preparer._promote_conform_into_cache(
                key,
                finished,
                source,
                _loudness(),
                False,
                media_duration_seconds=15949.2,
                loudness_method="ride",
            )
            promoted.append(True)
        return _loudness()

    racing = SourcePreparer(
        work_dir=tmp_path / "work",  # the SAME cache dir -- one shared cache
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=racing_loudness,
        warm_scheduler=lambda _job: None,
        playout_trim_supported=False,  # its own conform stays bounded, unpromoted
    )

    racing.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=30.0), _config())
    assert promoted, "the fixture must promote the asset during the probe"

    meta_after = json.loads((preparer._cache_dir() / f"{key}.json").read_text(encoding="utf-8"))
    assert meta_after["full_asset_conform"] is True, (
        "the probe-only write erased the promoted full-asset conform's marker"
    )
    assert meta_after["loudness_method"] == "ride"

    # The consequence the rung measured: the next prepare re-conforms the whole
    # asset instead of serving the entry already sitting in the cache.
    calls.clear()
    report = preparer.prepare(_slot_capped_untrimmed_plan(source, duration_seconds=30.0), _config())

    assert calls == [], "the promoted full-asset conform must still be a cache HIT"
    assert "conform-cache" in report.source_plan.segments[0].path


def test_default_warm_scheduler_runs_jobs_in_fifo_order_one_at_a_time(tmp_path: Path) -> None:
    """Item 66, point 4 (round-3 review tightened the assertions): the
    production ``_default_warm_scheduler`` used to spawn one daemon thread
    PER job -- unbounded. It must instead queue jobs onto a single worker:
    3 distinct assets queue 3 jobs, at most 1 ever runs concurrently, AND
    they complete in the order they were queued (``completed == [0, 1, 2]``,
    not merely ``sorted(completed) == [0, 1, 2]`` -- a worker that ran them
    out of order would still pass the weaker assertion). ``max_active`` is
    read under ``guard`` too, matching how it is written."""
    active = 0
    max_active = 0
    guard = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    completed: list[int] = []

    def make_job(n: int):
        def _job() -> None:
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            if n == 0:
                started.set()
                release.wait(timeout=5)
            with guard:
                active -= 1
            completed.append(n)

        return _job

    for n in range(3):
        preparer_module._default_warm_scheduler(make_job(n))

    assert started.wait(timeout=5)  # the first job is running
    with guard:
        assert max_active == 1  # never more than one warm job active at once
    release.set()
    # Give the single worker time to drain the remaining queued jobs.
    deadline = time.monotonic() + 5
    while len(completed) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert completed == [0, 1, 2]  # FIFO order, not just membership
    with guard:
        assert max_active == 1


def test_default_warm_scheduler_survives_a_job_that_raises(tmp_path: Path) -> None:
    """Item 66 round-3, point 5: a job that raises must not stop the
    single worker from draining the rest of the queue -- ``_warm_worker``'s
    own try/except is meant to catch exactly this, but it was never
    exercised by a test."""
    completed: list[int] = []

    def failing_job() -> None:
        completed.append(-1)
        raise RuntimeError("boom")

    def make_ok_job(n: int):
        def _job() -> None:
            completed.append(n)

        return _job

    preparer_module._default_warm_scheduler(failing_job)
    preparer_module._default_warm_scheduler(make_ok_job(1))

    deadline = time.monotonic() + 5
    while len(completed) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert completed == [-1, 1]  # job 2 still ran despite job 1's exception


# --- Candidate A: two-pass loudnorm conform ---------------------------------


def test_build_conform_source_args_without_probe_stays_single_pass(tmp_path: Path) -> None:
    """No measured metadata -> unchanged single-pass args (no behaviour drift)."""

    args = build_conform_source_args(
        source_path=tmp_path / "s.mp4",
        output_path=tmp_path / "o.ts",
        segment=None,
        profile=_config().canonical_profile,
        loudness_target_lufs=-16.0,
    )

    af = args[args.index("-af") + 1]
    assert af == "loudnorm=I=-16:LRA=11:TP=-1.5"


def test_build_conform_source_args_with_measured_metadata_is_two_pass(tmp_path: Path) -> None:
    """Measured metadata -> second-pass args carry measured_* + linear=true."""

    args = build_conform_source_args(
        source_path=tmp_path / "s.mp4",
        output_path=tmp_path / "o.ts",
        segment=None,
        profile=_config().canonical_profile,
        loudness_target_lufs=-16.0,
        measured_loudness=_loudnorm_probe_stderr_as_dict(),
    )

    af = args[args.index("-af") + 1]
    assert "measured_I=-23.01" in af
    assert "measured_LRA=22.90" in af
    assert "measured_TP=-0.05" in af
    assert "measured_thresh=-35.18" in af
    assert "offset=0.83" in af
    assert "linear=true" in af
    assert "I=-16" in af and "LRA=11" in af and "TP=-1.5" in af


def test_two_pass_args_reject_bad_measured_metadata(tmp_path: Path) -> None:
    """Malformed/incomplete measured metadata must fail loud."""

    for bad in (
        {"input_i": "-23.0"},
        {
            "input_i": "NaN",
            "input_tp": "-1",
            "input_lra": "5",
            "input_thresh": "-30",
            "target_offset": "0",
        },
        {
            "input_i": "-23",
            "input_tp": "inf",
            "input_lra": "5",
            "input_thresh": "-30",
            "target_offset": "0",
        },
        {},
    ):
        with pytest.raises(SourcePrepareError):
            build_conform_source_args(
                source_path=tmp_path / "s.mp4",
                output_path=tmp_path / "o.ts",
                segment=None,
                profile=_config().canonical_profile,
                loudness_target_lufs=-16.0,
                measured_loudness=bad,  # type: ignore[arg-type]
            )


def test_two_pass_does_not_mutate_source_and_keeps_output_path(tmp_path: Path) -> None:
    source = tmp_path / "s.mp4"
    source.write_bytes(b"original-bytes")
    before = source.read_bytes()

    args = build_conform_source_args(
        source_path=source,
        output_path=tmp_path / "o.ts",
        segment=None,
        profile=_config().canonical_profile,
        loudness_target_lufs=-16.0,
        measured_loudness=_loudnorm_probe_stderr_as_dict(),
    )

    assert source.read_bytes() == before
    assert args[-3:] == ["-f", "mpegts", str(tmp_path / "o.ts")]


def test_two_pass_probe_uses_same_window_as_encode(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(args, cancel_event=None):
        captured.append(list(args))
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=fake_run,
        loudness_checker=lambda **_k: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda job: None,
    )
    (tmp_path / "s.mp4").write_bytes(b"x")
    preparer.prepare(
        EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label="S",
                    path=str(tmp_path / "s.mp4"),
                    duration_seconds=30,
                    inpoint_seconds=5,
                    outpoint_seconds=35,
                )
            ],
        ),
        _config(),
    )
    probe = next(a for a in captured if "print_format=json" in " ".join(a))
    encode = next(a for a in captured if "print_format=json" not in " ".join(a))
    assert probe[probe.index("-ss") + 1] == encode[encode.index("-ss") + 1]
    assert probe[probe.index("-t") + 1] == encode[encode.index("-t") + 1]


def test_two_pass_cache_key_includes_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conform cache key must depend on the loudness-normalization method.

    Switching preparer.py from a one-pass ``loudnorm`` to a measured two-pass
    ``loudnorm`` changes the bytes of every conformed segment, so an entry
    written by the old method must never be served for the same source. The key
    mixes the module-level ``_LOUDNORM_METHOD_VERSION`` into its digest, so
    holding (source, profile, loudness config) fixed and *only* changing the
    method version yields a different key.
    """
    preparer = SourcePreparer(work_dir=tmp_path / "work")
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"x")
    config = _config()

    method = preparer_module._LOUDNORM_METHOD_VERSION
    baseline = preparer._cache_key(src, config)
    assert isinstance(baseline, str) and len(baseline) == 32
    # Same source, same config, same method -> same key (the cache still hits).
    assert preparer._cache_key(src, config) == baseline

    # Different method version -> different key (the stale one-pass entry is
    # unreachable, so the segment is re-conformed under the new method).
    monkeypatch.setattr(preparer_module, "_LOUDNORM_METHOD_VERSION", f"{method}+other")
    bumped = preparer._cache_key(src, config)
    assert bumped is not None
    assert bumped != baseline

    # Restoring the shipped version restores the shipped key: the key is a pure
    # function of these inputs, not of process history.
    monkeypatch.setattr(preparer_module, "_LOUDNORM_METHOD_VERSION", method)
    assert preparer._cache_key(src, config) == baseline

    # The shipped method version is the U25 ride one.  An older build's entry
    # must stay unreachable: a v2 two-pass conform and a v3 ridden one are
    # different bytes for the same window, and so are the two v3 shapes
    # themselves (ride vs the loudnorm fallback), which is why this bump
    # accompanies the wiring rather than a change to ``loudnorm``'s own args.
    assert method == "loudnorm-v3-ride"


def test_probe_args_carry_foreground_thread_cap(tmp_path: Path) -> None:
    cap = preparer_module._foreground_thread_cap()
    args = build_loudnorm_probe_args(
        source_path=tmp_path / "s.mp4",
        segment=None,
        loudness_target_lufs=-16.0,
        threads=cap,
    )
    assert args[args.index("-threads") + 1] == str(cap)
    assert args.index("-threads") < args.index("-i")
    for option in ("-filter_threads", "-filter_complex_threads"):
        assert args.count(option) == 1
        assert args[args.index(option) + 1] == str(cap)


def test_probe_threads_default_is_none(tmp_path: Path) -> None:
    args = build_loudnorm_probe_args(
        source_path=tmp_path / "s.mp4",
        segment=None,
        loudness_target_lufs=-16.0,
    )
    for option in ("-threads", "-filter_threads", "-filter_complex_threads"):
        assert option not in args


def test_synchronous_probe_passes_foreground_cap(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(args, cancel_event=None):
        captured.append(list(args))
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=fake_run,
        loudness_checker=lambda **_k: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda job: None,
    )
    (tmp_path / "s.mp4").write_bytes(b"x")
    preparer.prepare(
        EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(label="S", path=str(tmp_path / "s.mp4"), duration_seconds=30)
            ],
        ),
        _config(),
    )
    probe = next(a for a in captured if "print_format=json" in " ".join(a))
    assert probe[probe.index("-threads") + 1] == str(preparer_module._foreground_thread_cap())


def test_warm_full_asset_probe_uses_threads_1(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(args, cancel_event=None):
        captured.append(list(args))
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        Path(args[-1]).write_text("x", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(work_dir=tmp_path / "work", ffmpeg_runner=fake_run)
    (tmp_path / "s.mp4").write_bytes(b"x")
    loudness = _loudness(status="failed", measured_lufs=-18.0)
    prepared = preparer._conform_full_asset_into_cache(
        "k" * 32, tmp_path / "s.mp4", _config(), loudness, True, threads=1
    )
    assert prepared is not None
    probe = next(a for a in captured if "print_format=json" in " ".join(a))
    assert probe[probe.index("-threads") + 1] == "1"


def _loudnorm_probe_stderr_as_dict() -> dict[str, str]:
    return {
        "input_i": "-23.01",
        "input_tp": "-0.05",
        "input_lra": "22.90",
        "input_thresh": "-35.18",
        "target_offset": "0.83",
    }


# ---------------------------------------------------------------------------
# U20 — a failed loudnorm measurement pass DEGRADES, it does not block
# ---------------------------------------------------------------------------


def _probe_failing_runner(
    calls: list[list[str]],
    *,
    stderr: str = "ffmpeg version n7.1\nno json here\n",
    returncode: int = 0,
):
    """An ffmpeg runner whose loudnorm measurement pass yields nothing usable.

    The ``print_format=json`` probe answers with junk (or a non-zero exit);
    every other invocation behaves like a plain conform, creating the atomic
    write's ``.tmp`` file so the rename lands.
    """

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(list(args))
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=returncode, stdout="", stderr=stderr)
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_text("prepared", encoding="utf-8")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    return runner


def test_segment_probe_failure_degrades_to_a_single_pass_conform(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """U20 decision 1 (H5): a measurement pass that yields nothing usable must
    not fail the segment.  It logs ONE warning naming the source, the window and
    the reason, and conforms that window with the plain single-pass ``loudnorm``
    filter -- byte-for-byte what the installed station ships today."""

    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_probe_failing_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
    )

    with caplog.at_level(logging.WARNING, logger=preparer_module.__name__):
        report = preparer.prepare(_source_plan(tmp_path), _config())

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "measurement pass failed" in record.getMessage()
    ]
    assert len(warnings) == 1  # ONE warning, not one per segment/attempt
    message = warnings[0].getMessage()
    assert "raw-source.mp4" in message  # the source
    assert "'Council meeting' in=2s dur=12.5s" in message  # the exact window
    assert "no parseable loudnorm JSON" in message  # the reason

    conform = next(args for args in calls if "-af" in args)
    joined = " ".join(conform)
    assert "loudnorm=I=-24:LRA=11:TP=-1.5" in joined
    assert "measured_I=" not in joined  # single-pass: no measured_* parameters
    assert ":linear=true" not in joined
    assert all("measured_" not in " ".join(args) for args in calls)

    assert report.records[0].normalized is True
    assert report.records[0].loudness_method == "single-pass-fallback"


def test_segment_probe_nonzero_exit_degrades_and_names_the_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The degrade covers the other measured-failure shapes too: a probe that
    exits non-zero degrades with its own one-line reason rather than raising."""

    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_probe_failing_runner(calls, stderr="boom", returncode=1),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
    )
    with caplog.at_level(logging.WARNING, logger=preparer_module.__name__):
        report = preparer.prepare(_source_plan(tmp_path), _config())
    reasons = [
        record.getMessage()
        for record in caplog.records
        if "measurement pass failed" in record.getMessage()
    ]
    assert len(reasons) == 1
    assert "probe exited 1" in reasons[0]
    assert report.records[0].loudness_method == "single-pass-fallback"


def test_segment_probe_unusable_metadata_degrades(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A probe that exits 0 and emits parseable JSON whose values
    ``_validated_measured_loudness`` rejects degrades the same way."""

    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_probe_failing_runner(
            calls, stderr=_loudnorm_probe_stderr(input_i="not-a-number")
        ),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
    )
    with caplog.at_level(logging.WARNING, logger=preparer_module.__name__):
        report = preparer.prepare(_source_plan(tmp_path), _config())
    reasons = [
        record.getMessage()
        for record in caplog.records
        if "measurement pass failed" in record.getMessage()
    ]
    assert len(reasons) == 1
    assert "probe metadata unusable" in reasons[0]
    assert report.records[0].loudness_method == "single-pass-fallback"


def test_whole_asset_probe_failure_leaves_the_two_pass_cache_entry_empty(
    tmp_path: Path,
) -> None:
    """U20 decision 1 (H3) + the cache requirement: when the WHOLE-ASSET
    measurement degrades, no fallback artifact is cached under the two-pass
    key -- the segment airs from a bounded single-pass conform instead, and a
    later run whose probe succeeds populates the genuine two-pass entry."""

    src = tmp_path / "long-recording.mp4"
    plan = _untrimmed_plan(tmp_path)
    config = _config()

    calls: list[list[str]] = []
    degraded = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_probe_failing_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        playout_trim_supported=True,
    )
    first = degraded.prepare(plan, config)

    key = degraded._cache_key(src, config)
    assert key is not None  # the cache is on, so this key is the real one
    cache_dir = tmp_path / "work" / "conform-cache"
    # No whole-asset artifact at all: not the finished file, not a partial tmp.
    assert not (cache_dir / f"{key}.ts").exists()
    assert not (cache_dir / f"{key}.ts.tmp").exists()
    # The probe-only sidecar may exist, but it must claim neither a whole-asset
    # conform nor a two-pass method -- otherwise a later cache HIT would serve
    # this single-pass artifact as if a measurement had produced it.
    meta = degraded._read_cache_meta(key)
    assert meta is None or meta.get("full_asset_conform") is not True
    assert meta is None or meta.get("loudness_method") != "two-pass"

    assert first.records[0].loudness_method == "single-pass-fallback"
    assert "conform-cache" not in first.source_plan.segments[0].path
    conforms = [args for args in calls if "-af" in args]
    assert conforms, "the segment must still be conformed, just single-pass"
    assert all("measured_I=" not in " ".join(args) for args in conforms)

    # A later airing whose measurement pass works populates the REAL two-pass
    # entry -- nothing to replace, because the fallback never created one.
    second = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        playout_trim_supported=True,
    )
    recovered = second.prepare(plan, config)

    assert (cache_dir / f"{key}.ts").is_file()
    recovered_meta = second._read_cache_meta(key)
    assert recovered_meta is not None
    assert recovered_meta.get("full_asset_conform") is True
    assert recovered_meta.get("loudness_method") == "two-pass"
    assert "conform-cache" in recovered.source_plan.segments[0].path
    assert recovered.records[0].loudness_method == "two-pass"


def test_cache_hit_reports_the_method_the_entry_was_conformed_with(tmp_path: Path) -> None:
    """A cache HIT passes the method recorded in the sidecar through to the
    record, so a caller can tell an airing conformed two-pass from one that
    fell back to single-pass."""

    calls: list[list[str]] = []
    config = _config()
    plan = _untrimmed_plan(tmp_path)
    SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        playout_trim_supported=True,
    ).prepare(plan, config)

    hit = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        playout_trim_supported=True,
    ).prepare(plan, config)

    assert "conform-cache" in hit.source_plan.segments[0].path
    assert hit.records[0].loudness_method == "two-pass"


def test_probe_cancellation_still_propagates_instead_of_degrading(tmp_path: Path) -> None:
    """Cancellation is a shutdown/pause signal, not a measurement failure: it
    re-raises untouched, and nothing is conformed with the fallback filter."""

    calls: list[list[str]] = []

    def runner(args: list[str]) -> FfmpegResult:
        calls.append(list(args))
        if "print_format=json" in " ".join(args):
            raise SourcePreparationCancelledError("Source preparation was cancelled.")
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
    )

    with pytest.raises(SourcePreparationCancelledError):
        preparer.prepare(
            _source_plan(tmp_path),
            _config(),
            cancel_event=threading.Event(),
            protected_plan_dirs=frozenset(),
        )

    assert calls, "the probe must have run before the cancellation"
    assert all("print_format=json" in " ".join(args) for args in calls)


def test_conform_failure_after_a_successful_measurement_still_raises(tmp_path: Path) -> None:
    """Only the measurement pass degrades.  A real pass-2 error still fails the
    segment, two-pass or not."""

    def runner(args: list[str]) -> FfmpegResult:
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        return FfmpegResult(returncode=1, stdout="", stderr="boom")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
    )

    with pytest.raises(SourcePrepareError, match="could not be conformed"):
        preparer.prepare(_source_plan(tmp_path), _config())


# ---------------------------------------------------------------------------
# U36 item 7 (2026-09-25): never hand the worker a prepared segment that is not
# real media. A past-EOF copy-out measured 0 bytes on this station, and the
# GStreamer worker's own diagnosis of the discarded boundary reloads was
# ``gst_decode_bin_expose (): ... all streams without buffers`` -- what an
# empty or stream-less segment produces. Reject it HERE, naming the source and
# the in-point, instead of letting the worker abort the reload pre-commit and
# leave the boundary unprepared.
#
# The check's probe hook is the module-global
# ``_probe_prepared_segment_decodability``, so the tests that are NOT about the
# check stub it for the placeholder-writing fake runners, and the tests that ARE
# about it answer it ``False`` ("no stream") or ``None`` ("ffprobe could not be
# asked").
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _placeholder_output_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake ffmpeg runners in this module write placeholder bytes, not media,
    so the preparer's new decodability check (U36 item 7) would reject every one
    of them. Stub its probe hook as "yes, decodable"; a test that is about the
    check overrides this again with its own ``monkeypatch.setattr``.
    Deliberately NOT ``probe_media_duration_seconds``: several tests in this
    module fake THAT one to simulate an unknown-duration source asset, and the
    emitted-file question ("is what preparation just wrote playable at all")
    must not be answered by a fake aimed at the source-duration question.
    ``test_preparer_conform_cache_real_ffmpeg.py`` runs real ffmpeg and is a
    separate module, so it keeps the real probe."""
    monkeypatch.setattr(preparer_module, "_probe_prepared_segment_decodability", lambda _path: True)


def _u36_preparer(tmp_path: Path, runner: Callable[[list[str]], FfmpegResult]) -> SourcePreparer:
    return SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=lambda _job: None,
    )


def _u36_tail_plan(tmp_path: Path, *, inpoint: float, seconds: float) -> EgressSourcePlan:
    """The incident's shape: a copy-out whose in-point sits at (or past) the end
    of the source media -- the July 23 program re-resolved with the drifted
    schedule clock."""
    plan = _source_plan(tmp_path)
    return plan.model_copy(
        update={
            "segments": [
                plan.segments[0].model_copy(
                    update={
                        "inpoint_seconds": inpoint,
                        "outpoint_seconds": inpoint + seconds,
                        "duration_seconds": seconds,
                    }
                )
            ]
        }
    )


def _u36_rejection_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "prepared segment" in record.getMessage().lower()
    ]


def test_prepare_rejects_a_zero_byte_prepared_segment(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ffmpeg can exit 0 and still leave nothing playable: on this station a
    copy-out trimmed wholly past the media's end produced a 0-byte file
    (reports/U36.md section 1.4). Reject it before it reaches the worker."""

    def runner(args: list[str]) -> FfmpegResult:
        Path(args[-1]).write_bytes(b"")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    plan = _u36_tail_plan(tmp_path, inpoint=664.0, seconds=2.0)
    preparer = _u36_preparer(tmp_path, runner)

    with caplog.at_level(logging.WARNING), pytest.raises(SourcePrepareError):
        preparer.prepare(plan, _config(), protected_plan_dirs=frozenset())

    rejected = _u36_rejection_warnings(caplog)
    assert rejected, [r.getMessage() for r in caplog.records]
    text = rejected[0]
    # Names the source AND the in-point, so an operator can find the row.
    assert plan.segments[0].path in text
    assert "664" in text


def test_prepare_rejects_a_prepared_segment_with_no_decodable_stream(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg exited 0 and wrote bytes, but nothing in them is media --
    ``all streams without buffers`` in the worker's words. Same rejection."""

    def runner(args: list[str]) -> FfmpegResult:
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        preparer_module, "_probe_prepared_segment_decodability", lambda _path: False
    )
    plan = _u36_tail_plan(tmp_path, inpoint=664.0, seconds=2.0)
    preparer = _u36_preparer(tmp_path, runner)

    with caplog.at_level(logging.WARNING), pytest.raises(SourcePrepareError):
        preparer.prepare(plan, _config(), protected_plan_dirs=frozenset())

    rejected = _u36_rejection_warnings(caplog)
    assert rejected, [r.getMessage() for r in caplog.records]
    text = rejected[0]
    assert plan.segments[0].path in text
    assert "664" in text


def test_prepare_rejects_a_zero_byte_untrimmed_segment_naming_the_source_start(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An untrimmed segment carries ``inpoint_seconds=None`` (there is no ``-ss``
    to name), and the rejection must survive that: naming the source and saying
    "start" rather than crashing on a ``%.3f`` of ``None`` before it can log."""

    def runner(args: list[str]) -> FfmpegResult:
        Path(args[-1]).write_bytes(b"")
        return FfmpegResult(returncode=0, stdout="", stderr="")

    plan = _source_plan(tmp_path)
    plan = plan.model_copy(
        update={
            "segments": [
                plan.segments[0].model_copy(
                    update={"inpoint_seconds": None, "outpoint_seconds": None}
                )
            ]
        }
    )
    preparer = _u36_preparer(tmp_path, runner)

    with caplog.at_level(logging.WARNING), pytest.raises(SourcePrepareError) as excinfo:
        preparer.prepare(plan, _config(), protected_plan_dirs=frozenset())

    rejected = _u36_rejection_warnings(caplog)
    assert rejected, [r.getMessage() for r in caplog.records]
    assert plan.segments[0].path in rejected[0]
    assert "start" in rejected[0]
    assert "start" in str(excinfo.value)


def test_prepare_accepts_a_segment_when_the_decodability_probe_cannot_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unanswerable probe is NOT evidence of bad media. ``None`` means ffprobe
    is absent or could not run -- rejecting on it would fail every prepared
    segment on a box without ffmpeg, and would reject a playable file whose
    container reports no duration, which is what a duration probe cannot tell
    apart from a missing tool. Red evidence for this behaviour is the
    pre-existing real-ffmpeg test
    ``test_ffprobe_unavailable_never_promotes_a_fragment_as_full_asset`` failing
    at commit 733dc995 (it fakes ffprobe away and every segment came back
    rejected); this locks the same rule in at the unit level."""

    def runner(args: list[str]) -> FfmpegResult:
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(preparer_module, "_probe_prepared_segment_decodability", lambda _path: None)
    preparer = _u36_preparer(tmp_path, runner)

    report = preparer.prepare(_source_plan(tmp_path), _config(), protected_plan_dirs=frozenset())

    assert report.records[0].prepared_path.endswith("segment-0001.ts")


def test_prepare_accepts_a_decodable_prepared_segment(tmp_path: Path) -> None:
    """Not a blanket refusal: a prepared segment the probe can read reaches the
    worker exactly as before."""

    def runner(args: list[str]) -> FfmpegResult:
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = _u36_preparer(tmp_path, runner)

    report = preparer.prepare(_source_plan(tmp_path), _config(), protected_plan_dirs=frozenset())

    assert report.source_plan.channel_id == "gov"
    assert report.records[0].prepared_path.endswith("segment-0001.ts")


# U29 — the warm's timeout, priority and retry budget
#
# Observed on the live station (2026-09-25, reports/U29.md): the LPM rotation's
# 9020.834 s Sustainability Advisory Board meeting was warmed every ~30-minute
# rotation, timed out at the base 300 s, and re-armed forever -- a permanent
# background CPU burn competing with three live encoders and live caption ASR.
# ---------------------------------------------------------------------------

#: The measured asset from reports/U29.md: 1920x1080 h264, 9020.834 s.
_U29_LONG_ASSET_SECONDS = 9020.834


class _FakeClock:
    """A monotonic clock a test can move, for the six-hour backoff window."""

    def __init__(self) -> None:
        self._now = 1000.0

    def monotonic(self) -> float:
        return self._now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _warm_kwargs_capturing_runner(
    calls: list[dict[str, object]],
) -> Callable[..., FfmpegResult]:
    """A stand-in for ``run_ffmpeg`` that records the per-call keyword
    arguments the warm path is required to pass (U29 3a/3b).

    Install it as BOTH ``ffmpeg_runner`` and the module's ``run_ffmpeg`` name:
    the preparer only forwards ``timeout``/``lower_priority``/``cancel_event``
    when those two are the identical object (that identity check is exactly
    what keeps a one-argument test double's signature working), so a test that
    wants to SEE the forwarding must satisfy it.
    """

    def runner(
        args: list[str],
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        lower_priority: bool = False,
    ) -> FfmpegResult:
        calls.append({"args": args, "timeout": timeout, "lower_priority": lower_priority})
        if "print_format=json" in " ".join(args):
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    return runner


def _u29_warm_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: Callable[..., FfmpegResult],
) -> tuple[SourcePreparer, list[Callable[[], None]], Path, str, EgressConfig]:
    warm_jobs: list[Callable[[], None]] = []
    monkeypatch.setattr(preparer_module, "run_ffmpeg", runner)
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
    )
    source = tmp_path / "Sustainability_Advisory_Board_-_April_2026.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    return preparer, warm_jobs, source, key, config


def test_warm_timeout_scales_with_the_asset_and_keeps_the_base_as_a_floor() -> None:
    """U29 3a, the arithmetic itself: ``timeout = max(base, duration /
    design_speed * factor)``, bounded above by the ceiling, and the base
    timeout whenever the duration is unknown or tiny."""

    scaled = preparer_module.warm_preparation_timeout_seconds
    base = 300.0

    # Unknown / non-positive duration -> the caller's base timeout, untouched.
    assert scaled(None, base) == base
    assert scaled(0.0, base) == base
    assert scaled(-5.0, base) == base
    # A short asset designs far inside the base -> the base is the FLOOR, so
    # this can only ever widen a warm's budget, never shrink the air path's.
    assert scaled(60.0, base) == base

    # The measured 9020.834 s asset: 8x-realtime design speed, 1.5x safety.
    expected_long = (
        _U29_LONG_ASSET_SECONDS
        / preparer_module._WARM_CONFORM_DESIGN_SPEED_RPS
        * preparer_module._WARM_CONFORM_TIMEOUT_FACTOR
    )
    assert scaled(_U29_LONG_ASSET_SECONDS, base) == pytest.approx(expected_long)
    # Justify the design speed from the U29 measurements (reports/U29.md):
    # the measured worst case was 10.42x realtime with a foreground conform
    # running alongside (16.26x alone, at BELOW_NORMAL).  The budget must
    # cover that worst case with real headroom...
    assert scaled(_U29_LONG_ASSET_SECONDS, base) > 2 * 554.9
    # ...and must be strictly wider than the 300 s base that produced the
    # observed permanent re-warm loop for this very asset.
    assert scaled(_U29_LONG_ASSET_SECONDS, base) > base

    # Monotonic in duration, and never below the base.
    previous = 0.0
    for duration in (300.0, 900.0, 3600.0, 9020.834, 15949.2):
        value = scaled(duration, base)
        assert value >= base
        assert value >= previous
        previous = value
    # Ceiling: one warm worker serves every asset, so a pathological input
    # must not be able to hold the queue for a day.
    ceiling = preparer_module._WARM_CONFORM_TIMEOUT_CEILING_SECONDS
    assert scaled(24 * 3600.0, base) == ceiling


def test_long_asset_warm_uses_the_scaled_timeout_at_lower_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U29 3a/3b end-to-end through the queued warm job: both the loudnorm
    measurement pass AND the conform of a 2.5 h asset carry the SCALED
    timeout, and both run at lowered process priority."""

    calls: list[dict[str, object]] = []
    preparer, warm_jobs, source, key, config = _u29_warm_fixture(
        tmp_path, monkeypatch, _warm_kwargs_capturing_runner(calls)
    )

    preparer._schedule_warm(
        key,
        source,
        config,
        _loudness(),
        True,
        media_duration_seconds=_U29_LONG_ASSET_SECONDS,
    )
    assert len(warm_jobs) == 1
    warm_jobs[0]()

    expected = preparer_module.warm_preparation_timeout_seconds(
        _U29_LONG_ASSET_SECONDS, preparer._preparation_timeout_seconds
    )
    assert expected > preparer._preparation_timeout_seconds
    probes = [c for c in calls if "print_format=json" in " ".join(c["args"])]  # type: ignore[arg-type]
    conforms = [c for c in calls if "print_format=json" not in " ".join(c["args"])]  # type: ignore[arg-type]
    assert len(probes) == 1, "the warm measures before it conforms"
    assert len(conforms) == 1
    # The measurement pass decodes the WHOLE asset too -- a warm whose probe
    # times out measures nothing, degrades to single-pass and is discarded
    # (U20), so the scaled budget must cover BOTH passes.
    assert probes[0]["timeout"] == pytest.approx(expected)
    assert conforms[0]["timeout"] == pytest.approx(expected)
    assert probes[0]["lower_priority"] is True
    assert conforms[0]["lower_priority"] is True


def test_on_demand_preparation_keeps_the_base_timeout_and_normal_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U29 3a/3b's guard rail: the AIR path must not inherit either change.
    An untrimmed-miss airing of a 3600 s asset -- a duration that WOULD scale
    a warm's budget -- still runs both passes at the base timeout and at
    normal process priority, because a late segment is a channel outage and a
    busy box is not."""

    calls: list[dict[str, object]] = []
    runner = _warm_kwargs_capturing_runner(calls)
    monkeypatch.setattr(preparer_module, "run_ffmpeg", runner)
    warm_jobs: list[Callable[[], None]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        # A failed loudness gate -> the air path genuinely normalizes, so BOTH
        # of its passes (measurement + conform) are visible here.
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=warm_jobs.append,
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )

    preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert preparer._preparation_timeout_seconds == 300.0
    assert len(calls) == 2, "the air path measured and conformed"
    for call in calls:
        # The BASE timeout -- the air path's own budget, exactly as before U29.
        assert call["timeout"] == 300.0
        assert call["lower_priority"] is False
    # The same duration WOULD have been scaled had this asset gone to a warm.
    assert preparer_module.warm_preparation_timeout_seconds(3600.0, 300.0) > 300.0
    assert warm_jobs == []  # an untrimmed airing that populated the cache warms nothing


def test_a_failed_warm_backs_off_and_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """U29 3c: one failed warm for an asset stops that asset's warms for six
    hours, and the operator gets ONE warning -- naming the asset, its duration
    and the timeout it was given -- instead of an ERROR plus traceback every
    30-minute rotation."""

    mode = {"fail": True}

    def runner(
        args: list[str],
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        lower_priority: bool = False,
    ) -> FfmpegResult:
        if mode["fail"]:
            raise subprocess.TimeoutExpired(args, timeout=timeout or 300.0)
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer, warm_jobs, source, key, config = _u29_warm_fixture(tmp_path, monkeypatch, runner)

    # The backoff deadline is written with ``time.monotonic()`` at FAILURE time
    # and compared at SCHEDULING time, so both phases must read the same clock:
    # take control of ``time`` before the first failure, not after it.
    clock = _FakeClock()
    monkeypatch.setattr(preparer_module, "time", clock)

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.preparer"):
        preparer._schedule_warm(
            key,
            source,
            config,
            _loudness(),
            False,
            media_duration_seconds=_U29_LONG_ASSET_SECONDS,
        )
        assert len(warm_jobs) == 1
        warm_jobs[0]()  # the warm times out
        assert key not in preparer._warming  # not stuck -- the job cleaned up after itself

        # The next three airings of the same asset (its own rotation cadence)
        # must each enqueue nothing.
        for _ in range(3):
            preparer._schedule_warm(
                key,
                source,
                config,
                _loudness(),
                False,
                media_duration_seconds=_U29_LONG_ASSET_SECONDS,
            )
        assert len(warm_jobs) == 1, "a backed-off asset must not be re-queued"

        records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(records) == 1, [r.getMessage() for r in caplog.records]
        record = records[0]
        assert record.exc_info is None, "no traceback: this is a known, bounded condition"
        message = record.getMessage()
        expected_timeout = preparer_module.warm_preparation_timeout_seconds(
            _U29_LONG_ASSET_SECONDS, preparer._preparation_timeout_seconds
        )
        assert source.name in message
        assert f"{_U29_LONG_ASSET_SECONDS:g}s" in message  # the asset's duration
        assert f"{expected_timeout:g}s" in message  # the timeout it was given
        assert "retrying after 60s" in message  # initial bounded retry window

    # The window closes: the next airing warms again, and a warm that
    # succeeds clears the backoff entry outright.
    mode["fail"] = False
    preparer._schedule_warm(
        key,
        source,
        config,
        _loudness(),
        False,
        media_duration_seconds=_U29_LONG_ASSET_SECONDS,
    )
    assert len(warm_jobs) == 1, "still inside the six-hour window"
    clock.advance(preparer_module._WARM_FAILURE_BACKOFF_SECONDS + 1.0)
    preparer._schedule_warm(
        key,
        source,
        config,
        _loudness(),
        False,
        media_duration_seconds=_U29_LONG_ASSET_SECONDS,
    )
    assert len(warm_jobs) == 2, "the window closed, so the next airing warms again"
    warm_jobs[1]()
    assert key not in preparer._warm_backoff_until
    assert (tmp_path / "work" / "conform-cache" / f"{key}.ts").is_file()


def test_a_cancelled_warm_neither_warns_nor_backs_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """U29 3c boundary: cancellation is not a failure of the asset.

    A pause/shutdown mid-warm says nothing about whether this asset can be
    warmed, so it must not start a six-hour backoff (the station would then
    deny itself a warm it could have completed as soon as it resumed) and must
    not emit the operator-facing failure warning.  U20 already draws exactly
    this line for the measurement pass; this keeps the warm's own failure
    handling on the same side of it.
    """

    def runner(
        args: list[str],
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        lower_priority: bool = False,
    ) -> FfmpegResult:
        raise SourcePreparationCancelledError("Source preparation was cancelled.")

    preparer, warm_jobs, source, key, config = _u29_warm_fixture(tmp_path, monkeypatch, runner)

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.preparer"):
        preparer._schedule_warm(
            key,
            source,
            config,
            _loudness(),
            False,
            media_duration_seconds=_U29_LONG_ASSET_SECONDS,
        )
        assert len(warm_jobs) == 1
        warm_jobs[0]()

        assert key not in preparer._warming
        assert key not in preparer._warm_backoff_until, (
            "a cancellation must not start the six-hour backoff window"
        )
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []

        # ...and the very next airing is free to warm it again.
        preparer._schedule_warm(
            key,
            source,
            config,
            _loudness(),
            False,
            media_duration_seconds=_U29_LONG_ASSET_SECONDS,
        )
        assert len(warm_jobs) == 2


def test_on_demand_conform_shares_no_lock_or_queue_with_a_warm(tmp_path: Path) -> None:
    """U29 3d: an airing never queues behind a warm.  A warm holds this
    asset's per-key cache lock (exactly what ``_conform_full_asset_into_cache``
    does for the whole of its run) while an untrimmed airing of the SAME asset
    arrives.  The airing must (a) return promptly, (b) do its own bounded
    conform on the calling thread -- the runner really ran, inside the window
    where the lock was held -- and (c) not be executed by, or waited on by,
    the warm worker: enqueueing a warm-behind job is fine and expected, being
    run by that worker (or blocked behind one) is not."""

    calls: list[list[str]] = []
    warm_jobs: list[Callable[[], None]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(),
        warm_scheduler=warm_jobs.append,
        playout_trim_supported=True,  # the legacy ffmpeg-concat engine's trim-passthrough wiring
    )
    source = tmp_path / "long-recording.mp4"
    source.write_text("fake long media", encoding="utf-8")
    config = _config()
    key = preparer._cache_key(source, config)
    assert key is not None
    lock = preparer._conform_lock(key)
    release = threading.Event()

    def fake_long_warm() -> None:
        with lock:
            release.wait(timeout=5)

    warm_thread = threading.Thread(target=fake_long_warm, daemon=True)
    warm_thread.start()
    for _ in range(500):  # let the fake warm genuinely take the lock first
        if lock.locked():
            break
        time.sleep(0.01)
    assert lock.locked()

    started = time.monotonic()
    report = preparer.prepare(_untrimmed_plan(tmp_path), config)
    elapsed = time.monotonic() - started

    assert lock.locked(), "the warm was still holding the lock for the whole airing"
    assert elapsed < 1.0
    assert len(calls) == 1, "the airing ran its OWN conform, it did not wait"
    # Warm-behind is enqueued by the airing (that is the design), but the job
    # is never EXECUTED by this airing and this airing never waited for one.
    assert len(warm_jobs) == 1
    assert elapsed < 1.0
    seg = report.source_plan.segments[0]
    assert "conform-cache" not in seg.path  # its own per-plan file, not the warm's cache object
    assert Path(seg.path).is_file()

    release.set()
    warm_thread.join(timeout=5)


# U25 -- the ride is the conform, and loudnorm is the documented fallback
# ---------------------------------------------------------------------------


def test_trimmed_conform_rides_and_records_the_method(tmp_path: Path) -> None:
    """U25: a normalized window is leveled by the ride and muxed onto the source
    video, and the record says so.  The ride replaces the loudnorm pair
    entirely -- no measurement probe runs, and the only FFmpeg invocation is
    the program mux (source video + the ride's own audio, stream-copied)."""

    leveler = _StubLeveler()
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        window_leveler=leveler,
    )

    report = preparer.prepare(_source_plan(tmp_path), _config())

    assert report.records[0].loudness_method == "ride"
    assert report.records[0].normalized is True

    # One ride, called with this window's own terms.
    assert len(leveler.calls) == 1
    ride = leveler.calls[0]
    assert ride["source_path"] == tmp_path / "raw-source.mp4"
    assert ride["params"] == RideParams(target_lufs=-24.0)
    assert ride["duration_s"] == 12.5
    assert ride["segment"] is not None
    assert ride["segment"].label == "Council meeting"  # type: ignore[union-attr]
    assert ride["profile"] == _config().canonical_profile
    assert str(ride["audio_path"]).endswith(".ride-audio.ts")

    # One FFmpeg call: the program mux.  No loudnorm anywhere on this path.
    assert len(calls) == 1
    mux = calls[0]
    joined = " ".join(mux)
    assert "loudnorm" not in joined
    assert "print_format=json" not in joined
    assert mux[mux.index("-map") : mux.index("-map") + 4] == ["-map", "0:v:0", "-map", "1:a:0"]
    assert mux[mux.index("-c:a") + 1] == "copy"
    prepared = Path(report.records[0].prepared_path)
    assert Path(mux[-1]).name == prepared.name + ".tmp"
    assert prepared.is_file()

    # Neither the ride's audio nor the atomic-write tmp survives.
    assert list(tmp_path.rglob("*.ride-audio.ts")) == []
    assert list(tmp_path.rglob("*.tmp")) == []


def test_trimmed_conform_falls_back_to_loudnorm_when_the_ride_is_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """U25: the ride failing is a degradation, never a failure.  ONE warning
    names the source and the reason, and the window is conformed exactly as it
    was before U25 -- measure, then two-pass loudnorm."""

    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        window_leveler=_no_ride,
    )

    with caplog.at_level(logging.WARNING, logger=preparer_module.__name__):
        report = preparer.prepare(_source_plan(tmp_path), _config())

    assert report.records[0].loudness_method == "two-pass"
    assert any("print_format=json" in " ".join(args) for args in calls), "the probe must run"
    assert "loudnorm=I=-24" in " ".join(calls[-1])

    degradations = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "ride" in record.getMessage().lower()
    ]
    assert len(degradations) == 1, [r.getMessage() for r in caplog.records]
    message = degradations[0].getMessage()
    assert "raw-source.mp4" in message
    assert "test: the ride is not available" in message


def test_failed_ride_mux_leaves_no_tmp_for_the_fallback(tmp_path: Path) -> None:
    """U25: ``build_video_from_source_args`` carries no ``-y``, so a leftover
    tmp makes the fallback's own encode fail with "File exists".  A mux that
    exits non-zero must unlink its tmp, and its ride audio, before the loudnorm
    fallback starts."""

    state: dict[str, object] = {"mux_calls": 0, "tmps_at_probe": None}

    def runner(args: list[str]) -> FfmpegResult:
        joined = " ".join(args)
        if "print_format=json" in joined:
            state["tmps_at_probe"] = sorted(p.name for p in tmp_path.rglob("*.tmp"))
            return FfmpegResult(returncode=0, stdout="", stderr=_loudnorm_probe_stderr())
        if "-map" in args:
            state["mux_calls"] = int(state["mux_calls"]) + 1  # type: ignore[call-overload]
            return FfmpegResult(returncode=1, stdout="", stderr="boom")
        _write_fake_output(args)
        return FfmpegResult(returncode=0, stdout="", stderr="")

    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=runner,
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        window_leveler=_StubLeveler(),
    )

    report = preparer.prepare(_source_plan(tmp_path), _config())

    assert state["mux_calls"] == 1
    assert state["tmps_at_probe"] == [], "the ride's tmp must be gone before the fallback encodes"
    assert report.records[0].loudness_method == "two-pass"
    assert list(tmp_path.rglob("*.ride-audio.ts")) == []


def test_cancelled_ride_cancels_the_prepare_and_cleans_up(tmp_path: Path) -> None:
    """U25: a cancelled ride is not a degradation -- it must stop the prepare,
    exactly like a cancelled measurement or encode, and leave no audio behind."""

    leveler = _StubLeveler(cancel_on_call=0)
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner([]),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        window_leveler=leveler,
    )

    with pytest.raises(SourcePreparationCancelledError):
        preparer.prepare(
            _source_plan(tmp_path),
            _config(),
            cancel_event=threading.Event(),
            protected_plan_dirs=frozenset(),
        )

    assert len(leveler.calls) == 1
    assert list(tmp_path.rglob("*.ride-audio.ts")) == []


def test_full_asset_conform_rides_and_the_cache_meta_says_ride(tmp_path: Path) -> None:
    """U25: the whole-asset conform (the untrimmed path an engine that cannot
    trim at playout takes) rides too, over the WHOLE file -- no segment, no
    duration limit -- and both the record and the cache sidecar it leaves
    behind say ``ride`` rather than borrowing the loudnorm label."""

    leveler = _StubLeveler()
    calls: list[list[str]] = []
    preparer = SourcePreparer(
        work_dir=tmp_path / "work",
        ffmpeg_runner=_counting_runner(calls),
        loudness_checker=lambda **_kwargs: _loudness(status="failed", measured_lufs=-18.0),
        warm_scheduler=lambda _job: None,
        playout_trim_supported=True,
        window_leveler=leveler,
    )

    report = preparer.prepare(_untrimmed_plan(tmp_path), _config())

    assert report.records[0].loudness_method == "ride"
    assert len(leveler.calls) == 1
    assert leveler.calls[0]["segment"] is None
    assert leveler.calls[0]["duration_s"] is None
    assert "loudnorm" not in " ".join(calls[0])

    metas = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in tmp_path.rglob("*.json")
        if "full_asset_conform" in path.read_text(encoding="utf-8")
    ]
    assert len(metas) == 1
    assert metas[0]["loudness_method"] == "ride"
    assert metas[0]["full_asset_conform"] is True
    assert metas[0]["loudness_measurement_source"] == "input_probe"
