# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""The current emitted-artifact guard: decoded-sample bound, pad, variants, keep-best.

The guard measures the emitted artifact, spends a pad round when its decoded
sample peak exceeds ``TP_GUARD_MAX_PEAK_DBFS``, then tries AAC encoder variants
while the best measured attempt remains over the bound. Selection keeps the
best attempt by loudness gates, decoded peak, then emitted true peak. If no
attempt clears the bound, the best one still ships with a warning.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from civiccast.egress import loudness_ride as lr
from civiccast.egress.models import CanonicalProfile

#: Decoder and sink stand-ins for the tee test.  ``run_ride`` only ever hands
#: its two children opaque argument lists and a resolved binary, so pointing
#: ``_ffmpeg_binary`` at the test's own interpreter with ``-c`` scripts drives
#: the real chunk loop -- no FFmpeg build, and no re-implementation of it.
_DECODER_SCRIPT = """
import struct, sys
amp = float(sys.argv[1])
n = 60000
out = bytearray()
for i in range(n):
    v = amp * ((i % 1000) / 1000.0 - 0.5)
    out += struct.pack("<ff", v, v)
sys.stdout.buffer.write(bytes(out))
"""

_SINK_SCRIPT = """
import sys
Path = sys.argv[1]
data = sys.stdin.buffer.read()
with open(Path, "wb") as fh:
    fh.write(data)
"""


def _attempt(
    round_index: int = 0,
    limit_dbtp: float = -1.5,
    emitted_dbtp: float | None = None,
    emitted_lufs: float | None = -16.0,
    worst: float | None = 0.2,
    whole: float | None = 0.05,
    decoded_peak_dbfs: float | None = None,
) -> lr.LeveledAttempt:
    """One attempt as the driver reports it; loudness-lawful unless told otherwise."""
    return lr.LeveledAttempt(
        round_index=round_index,
        limit_dbtp=limit_dbtp,
        emitted_dbtp=emitted_dbtp,
        emitted_lufs=emitted_lufs,
        worst_window_err_lu=worst,
        whole_err_lu=whole,
        wall_s=0.0,
        decoded_peak_dbfs=decoded_peak_dbfs,
    )


def _select(attempts: list[lr.LeveledAttempt]) -> lr.LeveledSelection:
    return lr.select_leveled_attempt(attempts, window_tol_lu=0.9, whole_tol_lu=0.5)


# ---------------------------------------------------------------------------
# The hard gate
# ---------------------------------------------------------------------------


def test_the_gate_constants_are_the_answered_ones() -> None:
    assert lr.TP_GUARD_TARGET_DBTP == -1.0
    assert lr.TP_GUARD_MAX_PEAK_DBFS == 0.1
    assert lr.TP_GUARD_PAD_MARGIN_DB == 0.5
    assert lr.TP_GUARD_MAX_PAD_DB == 6.0
    # These superseded branches remain absent from the current guard API.
    assert not hasattr(lr, "TP_GUARD_LAST_RESORT_DBTP")
    assert not hasattr(lr, "TP_GUARD_HARD_DBTP")
    assert not hasattr(lr, "needs_last_resort")


def test_the_hard_gate_is_the_decoded_sample_bound() -> None:
    """One bound, measured on the artifact: no decoded sample above +0.1 dBFS.

    The emitted true peak is deliberately not part of it -- it is the codec's own
    overshoot, so an emitted-true-peak leg would only re-impose the ceiling
    arithmetic answer 12 abandoned.  ``+0.0036`` is BIG's nominal emit.
    """
    assert _attempt(decoded_peak_dbfs=0.0).hard_tp_ok() is True
    assert _attempt(decoded_peak_dbfs=0.1).hard_tp_ok() is True
    assert _attempt(decoded_peak_dbfs=0.0036).hard_tp_ok() is True
    assert _attempt(decoded_peak_dbfs=0.1001).hard_tp_ok() is False
    assert _attempt(decoded_peak_dbfs=0.4018).hard_tp_ok() is False
    assert _attempt(decoded_peak_dbfs=-1.3202).hard_tp_ok() is True
    # An unscanned artifact cannot clear a bound nobody measured.
    assert _attempt(decoded_peak_dbfs=None).hard_tp_ok() is False
    # ... and the emit's own true peak does not decide it.
    assert _attempt(emitted_dbtp=+2.8, decoded_peak_dbfs=-0.5).hard_tp_ok() is True
    assert _attempt(emitted_dbtp=-1.5, decoded_peak_dbfs=+0.5).hard_tp_ok() is False


def test_loudness_gates_are_unchanged_by_the_answer_11_reshuffle() -> None:
    assert _attempt(worst=0.9, whole=0.5).loudness_ok(window_tol_lu=0.9, whole_tol_lu=0.5)
    assert not _attempt(worst=0.91).loudness_ok(window_tol_lu=0.9, whole_tol_lu=0.5)
    assert not _attempt(worst=None).loudness_ok(window_tol_lu=0.9, whole_tol_lu=0.5)
    assert not _attempt(whole=None).loudness_ok(window_tol_lu=0.9, whole_tol_lu=0.5)


# ---------------------------------------------------------------------------
# The pad round and its decoded-peak arithmetic
# ---------------------------------------------------------------------------


def test_the_pad_is_sample_peak_overshoot_plus_margin() -> None:
    # The current pad responds to the measured decoded sample peak, not the
    # codec-inflated emitted true peak.
    assert lr.guard_pad_db(_attempt(decoded_peak_dbfs=0.1)) is None
    assert lr.guard_pad_db(_attempt(decoded_peak_dbfs=0.4)) == pytest.approx(0.8)


def test_the_pad_is_capped_at_the_configured_maximum() -> None:
    assert lr.guard_pad_db(_attempt(decoded_peak_dbfs=10.0)) == lr.TP_GUARD_MAX_PAD_DB


def test_the_pad_is_not_owed_without_a_measured_sample_peak_overshoot() -> None:
    assert lr.guard_pad_db(_attempt(decoded_peak_dbfs=-0.5, emitted_dbtp=0.0)) is None
    assert lr.guard_pad_db(_attempt(decoded_peak_dbfs=None, emitted_dbtp=0.0)) is None


# ---------------------------------------------------------------------------
# Selection: the loudness gates, then the lowest decoded peak, then the emit
# ---------------------------------------------------------------------------


def test_the_loudness_gates_outrank_the_lowest_decoded_peak() -> None:
    """The adverse-BIG shape, in the panel's own numbers.

    BIG's nominal round passed both loudness gates at a decoded peak of
    +0.0036 dBFS.  The single re-encode round came back 0.6 LU quieter, blew the
    window gate at 0.92 LU, and decoded **hotter** at +0.7979 dBFS.  Ranking the
    peak first would ship the second; answer 12 ranks the gates first, and the
    hard bound that follows is then judged on what ships.
    """
    big_nominal = _attempt(
        round_index=0,
        limit_dbtp=-1.5,
        emitted_dbtp=0.0,
        emitted_lufs=-16.0,
        worst=0.285,
        whole=0.0,
        decoded_peak_dbfs=0.0036,
    )
    big_reencode = _attempt(
        round_index=1,
        limit_dbtp=-2.8,
        emitted_dbtp=0.9,
        emitted_lufs=-16.6,
        worst=0.92,
        whole=-0.6,
        decoded_peak_dbfs=0.7979,
    )
    sel = _select([big_nominal, big_reencode])
    assert sel.kept.round_index == 0
    assert sel.hard_tp_met is True, "+0.0036 dBFS is inside the +0.1 bound"
    assert sel.target_met is False, "+0.00 dBTP misses the -1.0 dBTP target"
    assert sel.warning is not None
    assert "+0.00 dBFS" in sel.warning


def test_the_loudness_gates_outrank_the_coldest_emit() -> None:
    loud = _attempt(limit_dbtp=-1.5, emitted_dbtp=-0.4, decoded_peak_dbfs=-0.487)
    colder_not_loud = _attempt(
        round_index=1, limit_dbtp=-2.4, emitted_dbtp=-0.9, worst=1.4, decoded_peak_dbfs=-1.2
    )
    sel = _select([loud, colder_not_loud])
    assert sel.kept.round_index == 0
    assert sel.hard_tp_met is True
    assert sel.target_met is False
    assert sel.warning is not None


def test_the_lowest_decoded_peak_wins_among_lawful_attempts() -> None:
    """The peak axis is live: a colder *emit* does not outrank a colder *decode*."""
    lower_peak_hotter_tp = _attempt(limit_dbtp=-1.5, emitted_dbtp=-0.9, decoded_peak_dbfs=-1.30)
    colder_tp_higher_peak = _attempt(
        round_index=1, limit_dbtp=-2.8, emitted_dbtp=-1.5, decoded_peak_dbfs=-0.80
    )
    sel = _select([lower_peak_hotter_tp, colder_tp_higher_peak])
    assert sel.kept.round_index == 0


def test_the_coldest_lawful_emit_breaks_a_peak_tie() -> None:
    sel = _select(
        [
            _attempt(limit_dbtp=-1.5, emitted_dbtp=-1.2, decoded_peak_dbfs=-0.5),
            _attempt(round_index=1, limit_dbtp=-1.8, emitted_dbtp=-1.6, decoded_peak_dbfs=-0.5),
        ]
    )
    assert sel.kept.round_index == 1
    assert sel.target_met is True
    assert sel.warning is None


def test_an_unmeasurable_attempt_never_wins() -> None:
    sel = _select(
        [
            _attempt(emitted_dbtp=None, decoded_peak_dbfs=None),
            _attempt(round_index=1, limit_dbtp=-2.0, emitted_dbtp=-1.3, decoded_peak_dbfs=-0.9),
        ]
    )
    assert sel.kept.round_index == 1
    assert sel.warning is None


def test_an_unscanned_peak_cannot_win_the_peak_axis() -> None:
    """A peak nobody measured is not the lowest peak; it is no evidence at all."""
    unscanned = _attempt(limit_dbtp=-1.5, emitted_dbtp=-1.4, decoded_peak_dbfs=None)
    scanned = _attempt(round_index=1, limit_dbtp=-1.8, emitted_dbtp=-1.4, decoded_peak_dbfs=-0.05)
    sel = _select([unscanned, scanned])
    assert sel.kept.round_index == 1
    assert sel.hard_tp_met is True


def test_all_unmeasurable_keeps_the_nominal() -> None:
    sel = _select(
        [_attempt(emitted_dbtp=None), _attempt(round_index=1, limit_dbtp=-2.8, emitted_dbtp=None)]
    )
    assert sel.kept.round_index == 0
    assert sel.target_met is False
    assert sel.hard_tp_met is False
    assert sel.warning is not None
    assert "unmeasurable" in sel.warning


def test_ties_go_to_the_earlier_round() -> None:
    sel = _select(
        [
            _attempt(limit_dbtp=-1.5, emitted_dbtp=-1.4, decoded_peak_dbfs=-0.6),
            _attempt(round_index=1, limit_dbtp=-1.8, emitted_dbtp=-1.4, decoded_peak_dbfs=-0.6),
        ]
    )
    assert sel.kept.round_index == 0


def test_the_warning_names_both_true_peaks_and_the_peak() -> None:
    """One warning, and the numbers an operator needs: what ships, what was emitted."""
    sel = _select(
        [
            _attempt(limit_dbtp=-1.5, emitted_dbtp=0.3, decoded_peak_dbfs=-0.31),
            _attempt(round_index=1, limit_dbtp=-2.8, emitted_dbtp=-0.2, decoded_peak_dbfs=-0.55),
        ]
    )
    assert sel.kept.round_index == 1
    assert sel.target_met is False
    assert sel.warning is not None
    assert "+0.30 dBTP" in sel.warning
    assert "-0.20 dBTP" in sel.warning
    assert "-0.55 dBFS" in sel.warning


def test_no_warning_when_the_target_is_met() -> None:
    sel = _select([_attempt(emitted_dbtp=-1.2, decoded_peak_dbfs=-1.0)])
    assert sel.target_met is True
    assert sel.warning is None


def test_every_attempt_over_the_bound_still_ships_the_best_one() -> None:
    """An ERROR line, not a stop, and not another emit.

    Answer 12: nothing better exists to keep, so the best attempt airs and the
    caller reports it.  The guard must not answer a hot artifact with a quieter
    emit -- that is the last-resort mistake the panel measured.
    """
    hot = _attempt(limit_dbtp=-1.5, emitted_dbtp=0.4, decoded_peak_dbfs=0.30)
    hotter = _attempt(round_index=1, limit_dbtp=-2.8, emitted_dbtp=1.1, decoded_peak_dbfs=0.40)
    sel = _select([hot, hotter])
    assert sel.kept.round_index == 0
    assert sel.hard_tp_met is False
    assert sel.target_met is False
    assert sel.warning is not None
    assert "+0.30 dBFS" in sel.warning
    assert "NOT met" in sel.warning


# ---------------------------------------------------------------------------
# The re-encode round's grammar
# ---------------------------------------------------------------------------


def test_the_re_encode_reads_the_retained_pcm_and_shares_the_encode_tail() -> None:
    """The guard's round is the nominal encode with a file input, nothing else.

    Every option after the input leg is shared with :func:`build_encode_sink_args`
    by construction, so a change to the emitted codec, bitrate, rate, channels or
    container cannot reach one shape and miss the other.
    """
    profile = CanonicalProfile(video_codec="h264_mf")
    params = lr.RideParams(target_lufs=-16.0, limit_dbtp=-2.8)
    pcm = Path("C:/tmp/ride.pcm")
    out = Path("C:/tmp/guard.ts")

    nominal = lr.build_encode_sink_args(out, 1.25, params=params, profile=profile)
    guard = lr.build_reencode_args(pcm, out, trim_db=1.25, params=params, profile=profile)

    # Split at the input operand rather than at a hardcoded prefix length: what
    # this test is about is that the two shapes share a head and a tail and differ
    # only in where ``-i`` points, not how many options precede it.
    def _split(argv: list[str]) -> tuple[list[str], str, list[str]]:
        i = argv.index("-i")
        return argv[:i], argv[i + 1], argv[i + 2 :]

    n_head, n_in, n_tail = _split(nominal)
    g_head, g_in, g_tail = _split(guard)
    assert n_head == g_head
    assert n_head == [
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "f32le",
        "-ar",
        "48000",
        "-ac",
        "2",
    ]
    assert n_in == "pipe:0", "the encode sink takes the ride on stdin"
    assert g_in == str(pcm), "the guard's round takes the retained ride PCM"
    # Same tail, byte for byte: the re-encode is the encode sink's own grammar.
    assert g_tail == n_tail
    assert guard[guard.index("-c:a") + 1] == profile.audio_codec
    assert f"limit={lr.limit_value(-2.8):.6f}" in ",".join(guard)
    assert "asc=0:level=0" in ",".join(guard)


def test_the_two_shapes_differ_only_in_where_the_pcm_comes_from() -> None:
    profile = CanonicalProfile(video_codec="h264_mf")
    params = lr.RideParams(target_lufs=-16.0)
    nominal = lr.build_encode_sink_args(Path("o.ts"), 0.0, params=params, profile=profile)
    guard = lr.build_reencode_args(
        Path("p.pcm"), Path("o.ts"), trim_db=0.0, params=params, profile=profile
    )
    assert "pipe:0" in nominal and "pipe:0" not in guard


# ---------------------------------------------------------------------------
# The tee: the guard must re-encode the PCM the ride actually produced
# ---------------------------------------------------------------------------


def test_the_tee_writes_exactly_the_pcm_the_sink_receives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained PCM is the post-curve stream, byte for byte.

    A second render would be a second ride; the whole point of answer 11's round
    is that the limiter it re-runs sees the samples the nominal encode saw.  The
    tee is therefore not "close enough" -- it is asserted equal to what the sink
    read, and asserted equal to the curve applied to the decoded source.
    """
    monkeypatch.setattr(lr, "_ffmpeg_binary", lambda: sys.executable)
    pcm = tmp_path / "ride.pcm"
    sink_saw = tmp_path / "sink.bin"
    params = lr.RideParams(target_lufs=-16.0)

    rendered = lr.run_ride(
        decoder_args=["-c", _DECODER_SCRIPT, "0.5"],
        sink_args=["-c", _SINK_SCRIPT, str(sink_saw)],
        curve=[(0.0, 6.0), (10.0, 6.0)],
        params=params,
        pcm_path=pcm,
    )

    assert rendered.frames == 60000
    assert rendered.audio_seconds == round(60000 / params.sample_rate, 3)
    assert pcm.exists(), "run_ride must leave the tee for its caller to own"
    tee = pcm.read_bytes()
    assert tee == sink_saw.read_bytes()
    assert len(tee) == 60000 * 2 * 4

    samples = np.frombuffer(tee, dtype="<f4")
    # The decoder's ramp runs -0.5 .. +0.499 of ``amp``, so its hottest *absolute*
    # sample is the trough at ``amp * 0.5``, and a +6 dB curve scales amplitude by
    # 10**(6/20) -- not by 2.  Pinning the value is what proves the tee carries the
    # curve-applied stream and not, say, the decode.
    expected = 0.5 * 0.5 * 10.0 ** (6.0 / 20.0)
    assert samples.size == 120000
    assert abs(float(np.max(np.abs(samples))) - expected) < 1e-6


def test_run_ride_writes_no_tee_unless_it_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nominal path pays nothing for a facility its caller did not request."""
    monkeypatch.setattr(lr, "_ffmpeg_binary", lambda: sys.executable)
    sink_saw = tmp_path / "sink.bin"
    before = set(tmp_path.iterdir())
    lr.run_ride(
        decoder_args=["-c", _DECODER_SCRIPT, "0.5"],
        sink_args=["-c", _SINK_SCRIPT, str(sink_saw)],
        curve=[(0.0, 0.0)],
        params=lr.RideParams(target_lufs=-16.0),
    )
    assert set(tmp_path.iterdir()) == before | {sink_saw}


# ---------------------------------------------------------------------------
# The re-encode runner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threads", [None, 1, 3])
def test_run_reencode_reports_the_wall_time_and_raises_on_a_bad_pcm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, threads: int | None
) -> None:
    """A re-encode is a process like any other: it reports, and it fails loudly."""
    monkeypatch.setattr(lr, "_ffmpeg_binary", lambda: sys.executable)
    pcm = tmp_path / "ride.pcm"
    pcm.write_bytes(b"\x00\x00\x80?\x00\x00\x80?" * 2048)
    out = tmp_path / "guard.ts"
    calls: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        argv = list(args[0])  # type: ignore[arg-type]
        calls.append(argv)
        if argv[-1] == "-":
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        out.write_bytes(b"ts")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    # rc.8 MSI: the re-encode spawns through the same pinned seam as the
    # ride's own children, so the seam -- not subprocess.run -- is what a test
    # stands in for.
    monkeypatch.setattr(lr, "_run_child", fake_run)
    rendered = lr.run_reencode(
        pcm_path=pcm,
        output_path=out,
        trim_db=0.5,
        params=lr.RideParams(target_lufs=-16.0, limit_dbtp=-2.8),
        profile=CanonicalProfile(video_codec="h264_mf"),
        threads=threads,
    )
    assert rendered.wall_s >= 0.0
    assert calls, "run_reencode must run the builder's own argv"
    assert str(pcm) in calls[0]
    assert str(out) in calls[0]
    for option in ("-threads", "-filter_threads", "-filter_complex_threads"):
        if threads is None:
            assert option not in calls[0]
        else:
            assert calls[0][calls[0].index(option) + 1] == str(threads)


def test_run_reencode_raises_the_module_error_on_a_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lr, "_ffmpeg_binary", lambda: sys.executable)
    pcm = tmp_path / "ride.pcm"
    pcm.write_bytes(b"\x00" * 64)

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        argv = list(args[0])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(argv, 1, b"", b"Invalid data found")

    # rc.8 MSI: the re-encode spawns through the same pinned seam as the
    # ride's own children, so the seam -- not subprocess.run -- is what a test
    # stands in for.
    monkeypatch.setattr(lr, "_run_child", fake_run)
    with pytest.raises(lr.LoudnessRideError) as caught:
        lr.run_reencode(
            pcm_path=pcm,
            output_path=tmp_path / "guard.ts",
            trim_db=0.0,
            params=lr.RideParams(target_lufs=-16.0),
            profile=CanonicalProfile(video_codec="h264_mf"),
        )
    assert "Invalid data found" in str(caught.value)


# ---------------------------------------------------------------------------
# rc.8 MSI (2026-11-21): every ride child sits inside the air path's CPU limit
# ---------------------------------------------------------------------------


class _SeamChild:
    """The minimum of a child process the four ride entry points touch."""

    def __init__(self, argv: list[str], *, readable: bytes = b"", rc: int = 0) -> None:
        self.argv = argv
        self.pid = 4242
        self.returncode = rc

        class _Stream:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self.written = bytearray()

            def read(self, _n: int) -> bytes:
                return self._data

            def write(self, data: bytes) -> int:
                self.written.extend(data)
                return len(data)

            def close(self) -> None:
                return None

        self.stdout = _Stream(readable)
        self.stdin = _Stream(b"")
        self.stderr = _Stream(b"")

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        return b"", b""

    def poll(self) -> int:
        return self.returncode

    def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


def test_a_ride_child_is_spawned_below_normal_and_pinned_to_the_air_path_cores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Priority alone is not the ladder: ``BELOW_NORMAL`` orders a child's
    threads against the caption child's, but it does not stop two concurrent
    multi-threaded children (the ride's decoder and sink) from occupying every
    logical processor on the box. MSI rc.8: below-normal preparation ffmpeg
    children were observed with affinity on all 16 logical processors -- the
    ride's own children, the ones ``run_ffmpeg`` never started and so never
    pinned."""

    pinned: list[int] = []
    spawns: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(argv: list[str], **kwargs: object) -> _SeamChild:
        spawns.append((argv, kwargs))
        return _SeamChild(argv)

    monkeypatch.setattr(lr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lr, "pin_air_path_child", pinned.append)

    child = lr._spawn_child(["ffmpeg", "-version"], stdout=lr.subprocess.PIPE)

    assert child.pid == 4242
    assert pinned == [4242], "the child was started and never pinned"
    argv, kwargs = spawns[0]
    assert argv == ["ffmpeg", "-version"]
    assert kwargs["stdout"] is lr.subprocess.PIPE
    assert kwargs["creationflags"] == lr._CREATE_NO_WINDOW | lr._BELOW_NORMAL_PRIORITY_CLASS


def test_every_ride_process_goes_through_the_pinned_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ride child may bypass the seam.

    ``run_ride`` starts its decoder and sink concurrently, the leveler runs a
    measurement pass and (for a hot artifact) a re-encode; each of the four is
    preparation work and each must be spawned through ``_spawn_child``. Before
    the seam, all four went straight to ``subprocess`` with the priority flags
    alone -- the observed "affinity on all 16 logical processors"."""

    spawned: list[list[str]] = []

    def fake_spawn(argv: list[str], **_kwargs: object) -> _SeamChild:
        spawned.append(argv)
        return _SeamChild(argv)

    monkeypatch.setattr(lr, "_spawn_child", fake_spawn)
    monkeypatch.setattr(lr, "_ffmpeg_binary", lambda: "ffmpeg")
    params = lr.RideParams(target_lufs=-16.0)

    lr.run_ride(
        decoder_args=["-decoder"],
        sink_args=["-sink"],
        curve=[(0.0, 0.0)],
        params=params,
    )
    lr.run_capture(["-measure"])
    lr.run_reencode(
        pcm_path=tmp_path / "ride.pcm",
        output_path=tmp_path / "guard.ts",
        trim_db=0.0,
        params=params,
        profile=CanonicalProfile(video_codec="h264_mf"),
    )

    # decoder, sink, measurement and re-encode -- all four through the seam.
    assert len(spawned) == 4
    assert spawned[0][1] == "-decoder"
    assert spawned[1][1] == "-sink"
    assert spawned[2][1] == "-measure"
