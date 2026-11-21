# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""U25 answer 12: the acceptance gate, ported, and the leveling run that feeds it.

Answer 12 section 3 wires the guard into ``preparer.py``, and the guard's whole
input is a *measured artifact*: the ride has to measure the audio it just
emitted, on the acceptance harness's own arithmetic, and judge it.  Two things
therefore live in the module that did not before:

* the acceptance gate itself -- the 240 s window tiling in both families, the
  worst window deviation, the whole-program error -- a port of the harness the
  coordinator's U25 panel is scored by (``%TEMP%\\u25\\validate.py`` and
  ``%TEMP%\\u25\\r9\\model.py``), read over the same 100 ms M series
  :func:`civiccast.egress.loudness_ride.parse_ebur128_series` already returns;
* the leveling run: source series, :func:`converge`, the emitted pass with the
  guard's PCM tee, one re-encode round, and ``select_leveled_attempt``.

The ported arithmetic is deliberately *unrounded*.  The harness rounds every
number to 3 dp for its table; a gate the product ships on compares the value it
measured, not a display of it.  The tests below therefore pin the arithmetic to
hand-computed values and to the harness's own geometry (window starts, the
``duration - 1`` tail rule, ``start <= t < end``), so a drift in either shows up
as a wrong number rather than as a table that merely looks different.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from civiccast.egress import loudness_ride as lr
from civiccast.egress.models import CanonicalProfile, EgressSourceSegment


def test_short_tail_loudness_warning_keeps_actual_window_provenance():
    # The offset tiling's last window is only 10.2 seconds, not four minutes.
    levels = [-16.0] * 3600 + [-13.0] * 102
    attempt = lr.attempt_from_measurement(
        _stderr(levels),
        round_index=0,
        limit_dbtp=-2,
        target_lufs=-16,
        duration_s=370.2,
    )
    assert attempt.worst_window_start_s == 360
    assert attempt.worst_window_end_s == pytest.approx(370.2)
    assert attempt.worst_window_end_s - attempt.worst_window_start_s == pytest.approx(10.2)
    assert attempt.worst_window_err_lu > 0.9


# ---------------------------------------------------------------------------
# Canned ebur128 output.  The block line is the shape FFmpeg writes (and the
# module's own ``_RE_SERIES`` reads); the summary lines are the two the artifact
# attempt is built from.
# ---------------------------------------------------------------------------


def _stderr(
    values: list[float],
    *,
    step_s: float = 0.1,
    peak_dbtp: float | None = None,
    integrated_lufs: float | None = None,
) -> str:
    """One measurement pass's stderr: a block line per value, then the summary."""
    lines = [
        f"[Parsed_ebur128_0 @ 0x1] t: {0.1 + k * step_s:.1f} M: {v:.3f} S: -inf"
        for k, v in enumerate(values)
    ]
    if integrated_lufs is not None:
        lines.append(f"    I:         {integrated_lufs:.2f} LUFS")
    if peak_dbtp is not None:
        lines.append(f"    Peak:       {peak_dbtp:+.2f} dBFS")
    return "\n".join(lines) + "\n"


def _flat(value: float, *, seconds: float = 240.0, step_s: float = 0.1) -> list[float]:
    return [value] * round(seconds / step_s)


def _pairs(values: list[float], *, step_s: float = 0.1) -> list[tuple[float, float]]:
    """The ``(t, M)`` pairs those block lines parse to."""
    return [(round(0.1 + k * step_s, 4), v) for k, v in enumerate(values)]


# ---------------------------------------------------------------------------
# The gate's constants and its geometry
# ---------------------------------------------------------------------------


def test_the_window_constants_are_the_measured_ones() -> None:
    assert lr.LOUDNESS_WINDOW_S == 240.0
    assert lr.LOUDNESS_WINDOW_OFFSET_S == 120.0
    assert lr.LOUDNESS_WINDOW_TOL_LU == 0.9
    assert lr.LOUDNESS_WHOLE_TOL_LU == 0.5


def test_series_duration_is_the_last_block_plus_one_step() -> None:
    assert lr.series_duration_s(_pairs(_flat(-16.0, seconds=240.0))) == pytest.approx(240.1)
    assert lr.series_duration_s([]) == 0.0


def test_window_tiling_matches_the_harness_geometry() -> None:
    """Ten minutes, both families: starts 0/240/480 and 120/360.

    The harness tiles while ``start < duration - 1`` and clamps each window's end
    to the duration, so a 600 s artifact yields three windows from the zero
    family (the last one clamped, 480 -> 600) and two from the 120 s family.
    """
    series = _pairs(_flat(-16.0, seconds=600.0))
    zero = lr.window_levels(series, 600.0, offset_s=0.0)
    offset = lr.window_levels(series, 600.0, offset_s=lr.LOUDNESS_WINDOW_OFFSET_S)
    assert [round(start, 3) for start, _ in zero] == [0.0, 240.0, 480.0]
    assert [round(start, 3) for start, _ in offset] == [120.0, 360.0]
    assert all(level == pytest.approx(-16.0) for _, level in zero + offset)


def test_a_window_boundary_belongs_to_the_later_window() -> None:
    """``start <= t < end``: the block at exactly 240.0 s is the 240 s window's.

    One block 15 LU above the rest, sitting on the boundary.  The first window
    must not see it at all (its level stays exactly -16.0), and the second must
    (its level rises above -16.0).
    """
    values = _flat(-16.0, seconds=600.0)
    values[round(240.0 / 0.1) - 1] = -1.0
    assert _pairs(values)[2399][0] == pytest.approx(240.0)
    first, second = lr.window_levels(_pairs(values), 600.0, offset_s=0.0)[:2]
    assert first[1] == pytest.approx(-16.0)
    assert second[1] is not None and second[1] > -16.0


def test_the_tail_shorter_than_a_second_is_not_a_window() -> None:
    """``start < duration - 1``: a 240.5 s artifact has one window, 241.5 s has two."""
    series = _pairs(_flat(-16.0, seconds=241.5))
    assert len(lr.window_levels(series, 240.5, offset_s=0.0)) == 1
    assert len(lr.window_levels(series, 241.5, offset_s=0.0)) == 2


def test_a_window_of_pure_digital_silence_has_no_level() -> None:
    assert lr.window_levels(_pairs(_flat(lr.DIGITAL_SILENCE)), 240.0, offset_s=0.0)[0][1] is None


def test_the_worst_window_deviation_searches_both_families() -> None:
    """A 240 s stretch that is only inside one family's window still counts.

    Blocks from 120 s to 360 s sit 10 LU low.  The zero family sees half of them
    in each of two windows (2.599 LU of error -- the harness's own number, not a
    hand-computed one); the 120 s family sees all of them in one window -- 10.0 LU
    -- and that is the number the gate must report, at the window's own start.
    """
    series = [
        (t, -26.0 if 120.0 <= t <= 360.0 else v) for t, v in _pairs(_flat(-16.0, seconds=600.0))
    ]
    worst, start = lr.worst_window_deviation_lu(series, 600.0, target_lufs=-16.0)
    assert worst == pytest.approx(10.0)
    assert start == pytest.approx(120.0)
    zero_only = lr.worst_window_deviation_lu(series, 600.0, target_lufs=-16.0, offset_s=0.0)
    assert zero_only[0] == pytest.approx(2.599, abs=0.001)


def test_the_worst_window_deviation_is_none_when_nothing_was_measured() -> None:
    assert lr.worst_window_deviation_lu([], 240.0, target_lufs=-16.0) == (None, None)
    assert lr.worst_window_deviation_lu(
        _pairs(_flat(lr.DIGITAL_SILENCE)), 240.0, target_lufs=-16.0
    ) == (None, None)


def test_the_whole_program_error_is_the_gated_mean_of_the_series() -> None:
    """Whole-program ``I`` is the two-stage gated mean of the same M series.

    All blocks at -10 LUFS relative to a -16 target is exactly +6.0 LU of error,
    and it is signed: a program 4 LU quiet reads -4.0.
    """
    assert lr.whole_program_err_lu(_pairs(_flat(-10.0)), target_lufs=-16.0) == pytest.approx(6.0)
    assert lr.whole_program_err_lu(_pairs(_flat(-20.0)), target_lufs=-16.0) == pytest.approx(-4.0)
    assert lr.whole_program_err_lu([], target_lufs=-16.0) is None
    assert lr.whole_program_err_lu(_pairs(_flat(lr.DIGITAL_SILENCE)), target_lufs=-16.0) is None


# ---------------------------------------------------------------------------
# The two parsers the gate is scored with
# ---------------------------------------------------------------------------


def test_parse_true_peak_reads_the_summary_line() -> None:
    assert lr.parse_true_peak_dbtp("  True peak:\n    Peak:       -1.53 dBFS\n") == -1.53
    assert lr.parse_true_peak_dbtp("    Peak:       0.00 dBFS\n") == 0.0
    assert lr.parse_true_peak_dbtp("") is None
    assert lr.parse_true_peak_dbtp("    I:  -16.00 LUFS\n") is None


def test_parse_true_peak_takes_the_first_peak_line() -> None:
    """Harness parity: ``RE_TP.search`` -- the first match, not the last."""
    text = "    Peak:       -1.30 dBFS\n    Peak:       -2.90 dBFS\n"
    assert lr.parse_true_peak_dbtp(text) == -1.30


def test_parse_true_peak_ignores_the_per_frame_tpk_columns() -> None:
    """The per-block lines carry ``TPK:``, which is not a ``Peak:`` summary."""
    text = (
        "[Parsed_ebur128_0 @ 0x1] t: 0.1 M: -20.000 S: -inf"
        " I: -20.0 LUFS LRA: 0.0 LU FTPK: -3.0 -3.0 dBFS TPK: -2.9 -2.9 dBFS\n"
    )
    assert lr.parse_true_peak_dbtp(text) is None


# ---------------------------------------------------------------------------
# One attempt, out of one measurement
# ---------------------------------------------------------------------------


def test_attempt_from_measurement_keeps_the_printed_I_out_of_the_gate() -> None:
    """``emitted_lufs`` is the summary line; the gate is the series' own mean.

    They are different quantities on purpose: the summary ``I`` is what FFmpeg
    printed, the gate is what the acceptance harness computes off the same
    blocks.  Here they disagree (printed -16.0, series' gated mean -20.0) and the
    gate follows the series -- the panel's arithmetic, which puts the single
    240 s window 4.0 LU low and the whole program 4.0 LU low, not at target.
    """
    attempt = lr.attempt_from_measurement(
        _stderr(_flat(-20.0), peak_dbtp=-1.42, integrated_lufs=-16.0),
        round_index=1,
        limit_dbtp=-1.8,
        target_lufs=-16.0,
        duration_s=240.0,
        decoded_peak_dbfs=0.0036,
        wall_s=12.5,
    )
    assert attempt.round_index == 1
    assert attempt.limit_dbtp == pytest.approx(-1.8)
    assert attempt.emitted_lufs == pytest.approx(-16.0)
    assert attempt.emitted_dbtp == pytest.approx(-1.42)
    assert attempt.whole_err_lu == pytest.approx(-4.0)
    assert attempt.worst_window_err_lu == pytest.approx(4.0)
    assert attempt.decoded_peak_dbfs == pytest.approx(0.0036)
    assert attempt.wall_s == pytest.approx(12.5)
    assert attempt.loudness_ok(window_tol_lu=0.9, whole_tol_lu=0.5) is False
    assert attempt.hard_tp_ok() is True


def test_attempt_from_measurement_without_a_measurement_still_builds() -> None:
    """No series, no summary: every measured field is ``None``, nothing raises."""
    attempt = lr.attempt_from_measurement(
        "",
        round_index=0,
        limit_dbtp=-1.5,
        target_lufs=-16.0,
        duration_s=240.0,
    )
    assert attempt.emitted_lufs is None
    assert attempt.emitted_dbtp is None
    assert attempt.worst_window_err_lu is None
    assert attempt.whole_err_lu is None
    assert attempt.wall_s == 0.0
    assert attempt.decoded_peak_dbfs is None
    assert attempt.loudness_ok(window_tol_lu=0.9, whole_tol_lu=0.5) is False
    assert attempt.hard_tp_ok() is False


def test_attempt_duration_falls_back_to_the_series_coverage() -> None:
    """Without a stated duration the tiling uses the artifact's own coverage."""
    with_duration = lr.attempt_from_measurement(
        _stderr(_flat(-16.0)),
        round_index=0,
        limit_dbtp=-1.5,
        target_lufs=-16.0,
        duration_s=240.0,
    )
    without = lr.attempt_from_measurement(
        _stderr(_flat(-16.0)),
        round_index=0,
        limit_dbtp=-1.5,
        target_lufs=-16.0,
    )
    assert with_duration.worst_window_err_lu == pytest.approx(0.0)
    assert without.worst_window_err_lu == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# The two argument builders
# ---------------------------------------------------------------------------


def test_the_artifact_measure_args_measure_the_whole_file() -> None:
    """Seek-free, exactly like the harness's ``measure_whole``: no ``-ss``/``-t``."""
    args = lr.build_artifact_measure_args(artifact_path=Path("C:/cache/x.ts"))
    assert "-ss" not in args and "-t" not in args
    assert args[args.index("-i") + 1] == "C:/cache/x.ts" or args[args.index("-i") + 1] == str(
        Path("C:/cache/x.ts")
    )
    assert "-vn" in args
    assert "ebur128=peak=true" in args
    assert args[-2:] == ["-f", "null"] or args[-1] == "-"


def test_the_peak_scan_args_decode_to_raw_float32() -> None:
    params = lr.RideParams(target_lufs=-16.0)
    args = lr.build_peak_scan_args(artifact_path=Path("C:/cache/x.ts"), params=params)
    assert "-f" in args and args[args.index("-f") + 1] == "f32le"
    assert "-ar" in args and args[args.index("-ar") + 1] == "48000"
    assert "-ac" in args and args[args.index("-ac") + 1] == "2"
    assert "-vn" in args
    assert args[-1] == "-"


@pytest.mark.parametrize("threads", [None, 1, 3])
@pytest.mark.parametrize("copy_video", [False, True])
def test_the_program_mux_bounds_the_window_after_both_inputs(
    threads: int | None, copy_video: bool
) -> None:
    """``-t`` is an OUTPUT option, so it has to follow the LAST ``-i``.

    FFmpeg binds a ``-t`` sitting between two ``-i`` operands to the input that
    follows it -- here the ride's own audio, which is already exactly the
    window's length, so the bound became a no-op on the source.  Measured on a
    real 4 s window of an 8 s asset: the built program carried 7 s of video
    (the whole source tail past the in-point) against 3.97 s of audio, where
    ``build_conform_source_args``'s single-input ``-t`` produced 4 s.  The
    ``-ss`` stays before the FIRST ``-i``: that one is an input option on
    purpose.
    """
    segment = EgressSourceSegment(
        label="trimmed",
        path="C:/in/asset.mp4",
        duration_seconds=4.0,
        inpoint_seconds=1.0,
        outpoint_seconds=5.0,
    )
    args = lr.build_video_from_source_args(
        source_path=Path("C:/in/asset.mp4"),
        audio_path=Path("C:/in/program.ts.ride-audio.ts"),
        output_path=Path("C:/out/program.ts"),
        segment=segment,
        profile=CanonicalProfile(),
        threads=threads,
        copy_video=copy_video,
    )
    inputs = [i for i, arg in enumerate(args) if arg == "-i"]
    assert len(inputs) == 2, "the program mux reads the source and the ride's audio"
    assert args.index("-ss") < inputs[0], "the seek belongs to the source input"
    assert args.index("-t") > inputs[-1], "the window bound must be an output option"
    assert args[args.index("-t") + 1] == "4"
    options = ("-threads", "-filter_threads", "-filter_complex_threads")
    if threads is None:
        assert all(option not in args for option in options)
    else:
        codec_limits = [i for i, arg in enumerate(args) if arg == "-threads"]
        assert len(codec_limits) == 2
        assert codec_limits[0] < inputs[0] < inputs[-1] < codec_limits[1]
        assert all(args[index + 1] == str(threads) for index in codec_limits)
        for option in options[1:]:
            assert args.count(option) == 1
            assert args[args.index(option) + 1] == str(threads)


@pytest.mark.parametrize("builder", [lr.build_source_series_args, lr.build_decoder_args])
@pytest.mark.parametrize("threads", [None, 1, 3])
def test_source_audio_passes_cap_decoder_and_filter_threads(builder, threads: int | None) -> None:
    args = builder(
        source_path=Path("C:/in/asset.mp4"),
        segment=None,
        params=lr.RideParams(target_lufs=-16.0),
        threads=threads,
    )
    for option in ("-threads", "-filter_threads", "-filter_complex_threads"):
        if threads is None:
            assert option not in args
        else:
            assert args.count(option) == 1
            assert args[args.index(option) + 1] == str(threads)
            assert args.index(option) < args.index("-i")


@pytest.mark.parametrize("kind", ["measure", "encode", "reencode", "artifact", "peak"])
@pytest.mark.parametrize("threads", [None, 1, 3])
def test_remaining_ride_passes_cap_input_filters_and_output(kind, threads: int | None) -> None:
    params = lr.RideParams(target_lufs=-16.0)
    path = Path("C:/out/audio.ts")
    if kind == "measure":
        args = lr.build_measure_sink_args(0.5, params=params, threads=threads)
    elif kind == "encode":
        args = lr.build_encode_sink_args(
            path, 0.5, params=params, profile=CanonicalProfile(), threads=threads
        )
    elif kind == "reencode":
        args = lr.build_reencode_args(
            Path("C:/in/ride.pcm"),
            path,
            trim_db=0.5,
            params=params,
            profile=CanonicalProfile(),
            threads=threads,
        )
    elif kind == "artifact":
        args = lr.build_artifact_measure_args(artifact_path=path, threads=threads)
    else:
        args = lr.build_peak_scan_args(artifact_path=path, params=params, threads=threads)
    options = ("-threads", "-filter_threads", "-filter_complex_threads")
    if threads is None:
        assert all(option not in args for option in options)
    else:
        codec_limits = [i for i, arg in enumerate(args) if arg == "-threads"]
        assert len(codec_limits) == 2
        assert codec_limits[0] < args.index("-i") < codec_limits[1]
        assert all(args[index + 1] == str(threads) for index in codec_limits)
        for option in options[1:]:
            assert args.count(option) == 1
            assert args[args.index(option) + 1] == str(threads)


# ---------------------------------------------------------------------------
# The leveling run.  The two renderers are substituted (no encoder, no ride);
# the measurement is substituted too, so the run's own decisions -- which round
# ships, in which file -- are what the assertions read.
# ---------------------------------------------------------------------------

_NOMINAL = b"nominal-artifact"
_ROUND = b"round-artifact"
#: 240 s of blocks, so both window families exist (starts 0 and 120).
_WINDOW_S = 240.0
_FLAT = _flat(-16.0, seconds=_WINDOW_S)


class _Recorder:
    """A stand-in for the ride, the round, the capture and the scan.

    One object because the round's artifact path is only known once the round
    runs, and the measurement fakes have to key on it.
    """

    def __init__(
        self,
        *,
        nominal_stderr: str,
        nominal_peak: float | None,
        round_stderr: str = "",
        round_peak: float | None = None,
        round_error: Exception | None = None,
        capture_error: Exception | None = None,
        source_stderr: str = "",
    ) -> None:
        self.nominal_stderr = nominal_stderr
        self.nominal_peak = nominal_peak
        self.round_stderr = round_stderr
        self.round_peak = round_peak
        self.round_error = round_error
        self.capture_error = capture_error
        self.source_stderr = source_stderr or _stderr(_flat(-25.0, seconds=60.0), step_s=0.1)
        self.emitted_path: Path | None = None
        self.round_path: Path | None = None
        self.ride_calls: list[SimpleNamespace] = []
        self.round_calls: list[SimpleNamespace] = []
        self.measured: list[Path] = []
        self.scanned: list[Path] = []
        self.capture_args: list[list[str]] = []
        self.scan_threads: list[int | None] = []

    # -- the renderers ----------------------------------------------------
    def ride(
        self,
        *,
        decoder_args,
        sink_args,
        curve,
        params,
        pcm_path=None,
        cancel_event=None,
        timeout_s=None,
        lower_priority=False,
    ):
        self.ride_calls.append(
            SimpleNamespace(
                decoder_args=list(decoder_args),
                sink_args=list(sink_args),
                curve=list(curve),
                params=params,
                pcm_path=pcm_path,
                timeout_s=timeout_s,
            )
        )
        target = Path(sink_args[-1])
        if str(sink_args[-1]) == "-":
            # A probe pass: the measure sink discards, so it has no artifact.
            return lr.RideRender(
                frames=0,
                audio_seconds=0.0,
                wall_s=0.5,
                stderr=_stderr(_FLAT, integrated_lufs=-16.0),
            )
        if pcm_path is not None:
            Path(pcm_path).write_bytes(b"tee")
        target.write_bytes(_NOMINAL)
        self.emitted_path = target
        return lr.RideRender(frames=0, audio_seconds=0.0, wall_s=1.5, stderr="")

    def reencode(
        self,
        *,
        pcm_path,
        output_path,
        trim_db,
        params,
        profile,
        timeout_s=None,
        variant=None,
        threads=None,
        lower_priority=False,
    ):
        self.round_calls.append(
            SimpleNamespace(
                pcm_path=pcm_path,
                output_path=output_path,
                trim_db=trim_db,
                params=params,
                timeout_s=timeout_s,
                variant=variant,
                threads=threads,
            )
        )
        if self.round_error is not None:
            raise self.round_error
        Path(output_path).write_bytes(_ROUND)
        self.round_path = Path(output_path)
        return lr.RideRender(frames=0, audio_seconds=0.0, wall_s=0.5, stderr="")

    # -- the probes -------------------------------------------------------
    def capture(self, args, *, timeout_s=None, cancel_event=None, lower_priority=False):
        self.capture_args.append(list(args))
        path = Path(args[args.index("-i") + 1])
        self.measured.append(path)
        if self.capture_error is not None:
            raise self.capture_error
        if len(self.measured) == 1:
            # The run's first measurement is the source series -- the only pass
            # that is allowed to read the source file.
            return self.source_stderr
        if path.name == "x.capture":
            raise AssertionError(f"the run measured an unexpected path: {path}")
        if self.emitted_path is not None and path == self.emitted_path:
            if self.round_path is not None and self.round_path.exists() is False:
                raise AssertionError("the nominal artifact was overwritten before it was judged")
            return self.nominal_stderr
        if self.round_path is not None and path == self.round_path:
            return self.round_stderr
        return self.source_stderr

    def scan(
        self,
        artifact_path,
        *,
        params,
        timeout_s=None,
        cancel_event=None,
        threads=None,
        lower_priority=False,
    ):
        self.scan_threads.append(threads)
        path = Path(artifact_path)
        self.scanned.append(path)
        if self.round_path is not None and path == self.round_path:
            return self.round_peak
        return self.nominal_peak


def _run(tmp_path: Path, monkeypatch, rec: _Recorder, **kwargs):
    monkeypatch.setattr(lr, "run_capture", rec.capture)
    monkeypatch.setattr(lr, "scan_peak_dbfs", rec.scan)
    source = tmp_path / "x.capture"
    source.write_bytes(b"source")
    audio = tmp_path / "audio.ts"
    return lr.level_window(
        source_path=source,
        audio_path=audio,
        params=lr.RideParams(target_lufs=-16.0),
        profile=CanonicalProfile(video_codec="h264_mf"),
        duration_s=_WINDOW_S,
        ride_runner=rec.ride,
        reencode_runner=rec.reencode,
        **kwargs,
    )


@pytest.mark.parametrize("threads", [None, 1, 3])
def test_level_window_keeps_the_round_that_clears_the_gates_with_the_lower_peak(
    tmp_path: Path, monkeypatch, threads: int | None
) -> None:
    """Answer 12's keep-best, end to end: the nominal is hot, the round is lawful.

    The nominal emitted at -0.60 dBTP with a decoded peak of +0.40 dBFS -- over
    the hard bound -- while its loudness gates hold; the single round at a lower
    ceiling emits -1.60 dBTP with a peak of -0.20 dBFS and holds loudness too.
    Both clear the loudness gates, so the peek at the lowest decoded peak
    decides, and the round wins.  The artifact that airs must then *be* the
    round's: same bytes, in the caller's path.

    The round's ceiling follows the decoded sample peak: the nominal ceiling
    (-1.5) down by the overshoot past the +0.1 dBFS bound (0.3) plus the 0.5 dB
    margin, i.e. -2.3 dBTP.
    """
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-0.60, integrated_lufs=-16.0),
        nominal_peak=0.40,
        round_stderr=_stderr(_FLAT, peak_dbtp=-1.60, integrated_lufs=-16.0),
        round_peak=-0.20,
    )
    result = _run(tmp_path, monkeypatch, rec, threads=threads)

    selection = result.selection
    assert [a.round_index for a in selection.attempts] == [0, 1]
    assert selection.kept.round_index == 1
    assert selection.kept.limit_dbtp == pytest.approx(-2.3)
    assert selection.hard_tp_met is True
    assert selection.target_met is True
    assert selection.warning is None
    assert result.audio_path.read_bytes() == _ROUND
    # The pad round's decoded peak clears the bound, so encoder variants are
    # not spent. It re-encodes the nominal emit's tee at the measured ceiling.
    assert len(rec.round_calls) == 1
    assert rec.round_calls[0].pcm_path is not None
    assert rec.round_calls[0].params.limit_dbtp == pytest.approx(-2.3)
    assert rec.round_calls[0].trim_db == pytest.approx(result.curve.trim_db + 0.8)
    assert rec.round_calls[0].threads == threads
    assert rec.scan_threads and all(value == threads for value in rec.scan_threads)
    all_args = rec.capture_args + [
        args for call in rec.ride_calls for args in (call.decoder_args, call.sink_args)
    ]
    for args in all_args:
        for option in ("-threads", "-filter_threads", "-filter_complex_threads"):
            if threads is None:
                assert option not in args
            else:
                assert args[args.index(option) + 1] == str(threads)


def test_level_window_spends_no_round_when_the_nominal_emitted_at_target(
    tmp_path: Path, monkeypatch
) -> None:
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-1.40, integrated_lufs=-16.0),
        nominal_peak=-0.30,
        round_stderr=_stderr(_FLAT, peak_dbtp=-1.90, integrated_lufs=-16.0),
        round_peak=-0.50,
    )
    result = _run(tmp_path, monkeypatch, rec)
    assert rec.round_calls == []
    assert result.selection.kept.round_index == 0
    assert result.selection.hard_tp_met is True
    assert result.selection.target_met is True
    assert result.selection.warning is None
    assert result.audio_path.read_bytes() == _NOMINAL


def test_level_window_ships_the_artifact_when_its_peak_cannot_be_measured(
    tmp_path: Path, monkeypatch
) -> None:
    """An unscanned artifact cannot clear the hard gate, and still airs.

    The emitted true peak is at target, so no round is warranted; the missing
    peak is not a round's job to fix.  The caller's cue for its error line is
    ``hard_tp_met`` being false, with ``decoded_peak_dbfs`` ``None`` to say why.
    """
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-1.40, integrated_lufs=-16.0),
        nominal_peak=None,
    )
    result = _run(tmp_path, monkeypatch, rec)
    assert rec.round_calls == []
    assert result.selection.hard_tp_met is False
    assert result.selection.kept.decoded_peak_dbfs is None
    assert result.selection.warning is None
    assert result.audio_path.read_bytes() == _NOMINAL


def test_level_window_warns_when_the_kept_attempt_missed_the_tp_target(
    tmp_path: Path, monkeypatch
) -> None:
    """All guard attempts stay hot: the best one ships with the guard's warning."""
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-0.60, integrated_lufs=-16.0),
        nominal_peak=0.40,
        round_stderr=_stderr(_FLAT, peak_dbtp=-0.55, integrated_lufs=-16.0),
        round_peak=0.35,
    )
    result = _run(tmp_path, monkeypatch, rec)
    selection = result.selection
    assert len(rec.round_calls) == 1 + len(
        lr.encoder_variants_for(CanonicalProfile(video_codec="h264_mf"))
    )
    assert selection.kept.round_index == 1
    assert selection.target_met is False
    assert selection.hard_tp_met is False
    assert selection.warning is not None
    assert "-1.0 dBTP" in selection.warning
    assert "NOT met" in selection.warning
    assert result.audio_path.read_bytes() == _ROUND


def test_level_window_ships_the_artifact_when_its_measurement_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed measurement pass is a report, not a discarded ride.

    The artifact exists and is lawful as far as anyone knows; only the evidence
    for it is missing.  ``LoudnessRideError`` is swallowed here (the emitted
    true peak is unknown, so the guard cannot ask for a round either) and the
    attempt carries ``None`` throughout.
    """
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, integrated_lufs=-16.0),
        nominal_peak=-0.30,
        capture_error=lr.LoudnessRideError("the measurement child exited 1"),
    )
    monkeypatch.setattr(lr, "run_capture", rec.capture)
    monkeypatch.setattr(lr, "scan_peak_dbfs", rec.scan)
    source = tmp_path / "x.capture"
    source.write_bytes(b"source")
    audio = tmp_path / "audio.ts"
    emitted = _stderr(_FLAT, integrated_lufs=-16.0)

    def capture(args, *, timeout_s=None, cancel_event=None, lower_priority=False):
        path = Path(args[args.index("-i") + 1])
        if path == source:
            return rec.source_stderr
        raise lr.LoudnessRideError("the measurement child exited 1")

    monkeypatch.setattr(lr, "run_capture", capture)
    result = lr.level_window(
        source_path=source,
        audio_path=audio,
        params=lr.RideParams(target_lufs=-16.0),
        profile=CanonicalProfile(video_codec="h264_mf"),
        duration_s=_WINDOW_S,
        ride_runner=rec.ride,
        reencode_runner=rec.reencode,
    )
    assert emitted is not None  # the canned text above is a measurement, not an error
    kept = result.selection.kept
    assert kept.round_index == 0
    assert kept.worst_window_err_lu is None and kept.whole_err_lu is None
    assert kept.decoded_peak_dbfs == pytest.approx(-0.30)
    assert result.selection.hard_tp_met is True
    assert audio.read_bytes() == _NOMINAL


def test_level_window_measures_the_artifact_it_wrote_not_the_source(
    tmp_path: Path, monkeypatch
) -> None:
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-1.40, integrated_lufs=-16.0),
        nominal_peak=-0.30,
    )
    result = _run(tmp_path, monkeypatch, rec)
    source = tmp_path / "x.capture"
    assert rec.measured[0] == source
    assert rec.measured[-1] == result.audio_path
    assert rec.scanned == [result.audio_path]


def test_level_window_propagates_cancellation_instead_of_shipping(
    tmp_path: Path, monkeypatch
) -> None:
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, integrated_lufs=-16.0),
        nominal_peak=-0.30,
    )
    source = tmp_path / "x.capture"
    source.write_bytes(b"source")

    def capture(args, *, timeout_s=None, cancel_event=None, lower_priority=False):
        path = Path(args[args.index("-i") + 1])
        if path == source:
            return rec.source_stderr
        raise lr.LoudnessRideCancelledError("cancelled")

    monkeypatch.setattr(lr, "run_capture", capture)
    monkeypatch.setattr(lr, "scan_peak_dbfs", rec.scan)
    with pytest.raises(lr.LoudnessRideCancelledError):
        lr.level_window(
            source_path=source,
            audio_path=tmp_path / "audio.ts",
            params=lr.RideParams(target_lufs=-16.0),
            profile=CanonicalProfile(video_codec="h264_mf"),
            duration_s=_WINDOW_S,
            ride_runner=rec.ride,
            reencode_runner=rec.reencode,
        )


def test_level_window_leaves_a_caller_supplied_tee_alone(tmp_path: Path, monkeypatch) -> None:
    """The tee outlives the call: it is the caller's file, and both passes read it."""
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-0.60, integrated_lufs=-16.0),
        nominal_peak=0.40,
        round_stderr=_stderr(_FLAT, peak_dbtp=-1.60, integrated_lufs=-16.0),
        round_peak=-0.20,
    )
    tee = tmp_path / "ride.pcm"
    result = _run(tmp_path, monkeypatch, rec, pcm_path=tee)
    assert result.audio_path.read_bytes() == _ROUND
    assert tee.exists()
    emit_calls = [c for c in rec.ride_calls if str(c.sink_args[-1]) != "-"]
    assert len(emit_calls) == 1 and emit_calls[0].pcm_path == tee
    assert rec.round_calls[0].pcm_path == tee


def test_level_window_deletes_the_tee_it_owns(tmp_path: Path, monkeypatch) -> None:
    """``pcm_path=None``: the module tees into its own temp file and cleans it up."""
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-0.60, integrated_lufs=-16.0),
        nominal_peak=0.40,
        round_stderr=_stderr(_FLAT, peak_dbtp=-1.60, integrated_lufs=-16.0),
        round_peak=-0.20,
    )
    result = _run(tmp_path, monkeypatch, rec)
    emit_calls = [c for c in rec.ride_calls if str(c.sink_args[-1]) != "-"]
    owned = emit_calls[0].pcm_path
    assert owned is not None
    assert not Path(owned).exists()
    assert result.audio_path.exists()


def test_level_window_keeps_nominal_when_all_guard_rounds_fail(tmp_path: Path, monkeypatch) -> None:
    """Failed pad and variant rounds are reported; the nominal still ships.

    Each encoder attempt fails, leaving the nominal artifact in place -- the
    bytes that air are still the bytes the nominal emit wrote -- and the guard
    keeps round 0.
    """
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-0.60, integrated_lufs=-16.0),
        nominal_peak=0.40,
        round_error=lr.LoudnessRideError("the guard's re-encode exited 1"),
    )
    result = _run(tmp_path, monkeypatch, rec)
    assert len(rec.round_calls) == 1 + len(
        lr.encoder_variants_for(CanonicalProfile(video_codec="h264_mf"))
    )
    assert result.selection.kept.round_index == 0
    assert result.audio_path.read_bytes() == _NOMINAL
    # The first failure is reported, and no partial round file survives.
    assert result.round_error is not None
    assert "exited 1" in result.round_error
    assert rec.round_path is None


def test_level_window_keeps_the_nominal_when_all_rounds_peak_higher(
    tmp_path: Path, monkeypatch
) -> None:
    """The peak axis outranks the true-peak axis, and a losing render is deleted.

    Both attempts hold loudness, so answer 12's key moves to the lowest decoded
    peak -- and the nominal's +0.40 dBFS beats the round's +0.55, even though the
    round emitted the better true peak (-1.60 against -0.60).  The artifact that
    airs is the nominal's, and the round's file does not outlive the decision.
    """
    rec = _Recorder(
        nominal_stderr=_stderr(_FLAT, peak_dbtp=-0.60, integrated_lufs=-16.0),
        nominal_peak=0.40,
        round_stderr=_stderr(_FLAT, peak_dbtp=-1.60, integrated_lufs=-16.0),
        round_peak=0.55,
    )
    result = _run(tmp_path, monkeypatch, rec)
    selection = result.selection
    assert len(rec.round_calls) == 1 + len(
        lr.encoder_variants_for(CanonicalProfile(video_codec="h264_mf"))
    )
    assert selection.kept.round_index == 0
    assert selection.kept.decoded_peak_dbfs == pytest.approx(0.40)
    assert selection.target_met is False
    assert selection.warning is not None
    assert result.audio_path.read_bytes() == _NOMINAL
    assert rec.round_path is not None
    assert rec.round_path != result.audio_path
    assert not rec.round_path.exists()
