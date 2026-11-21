# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Contracts for v1.1 ITU-R BS.1770 / EBU R128 loudness compliance."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path


class TestLoudnessComplianceRealFfmpeg:
    def test_loudness_gate_uses_ffmpeg_wrapper_and_reports_minus_16_lufs(
        self,
        tmp_path: Path,
    ) -> None:
        loudness_module = import_module("civiccast.stream.loudness")

        result = loudness_module.check_streaming_loudness(
            media_path=tmp_path / "meeting.wav",
            target_lufs=-16.0,
            tolerance_lufs=1.0,
        )

        assert result.standard == "ITU-R BS.1770 / EBU R128"
        assert result.target_lufs == -16.0
        assert result.used_ffmpeg_wrapper is True
        assert result.status in {"ok", "failed"}
        assert result.status != "failed" or result.operator_action

    def test_loudness_gate_parses_integrated_lufs_from_ffmpeg(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        loudness_module = import_module("civiccast.stream.loudness")

        media = tmp_path / "meeting.wav"
        media.write_bytes(b"RIFFplaceholder")

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(
            loudness_module,
            "run_ffmpeg",
            lambda _args: type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
                },
            )(),
        )

        result = loudness_module.check_streaming_loudness(
            media_path=media,
            target_lufs=-16.0,
            tolerance_lufs=1.0,
        )

        assert result.status == "ok"
        assert result.measured_lufs == -16.2

    def test_loudness_probe_decodes_audio_only(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        """Item 66 follow-up (measured on HALO): an ebur128 pass on a 39-minute
        clip took 46.7s -- it decodes video too, since nothing told ffmpeg not
        to. ``-vn`` drops the video stream from the loudness probe entirely;
        it never changes the LUFS measurement (video frames never feed
        ebur128)."""
        loudness_module = import_module("civiccast.stream.loudness")

        media = tmp_path / "meeting.wav"
        media.write_bytes(b"RIFFplaceholder")

        captured: dict[str, list[str]] = {}

        def _fake_run_ffmpeg(args: list[str]):
            captured["args"] = args
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
                },
            )()

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(loudness_module, "run_ffmpeg", _fake_run_ffmpeg)

        loudness_module.check_streaming_loudness(
            media_path=media,
            target_lufs=-16.0,
            tolerance_lufs=1.0,
        )

        assert "-vn" in captured["args"]

    def test_probe_window_bounds_seek_and_duration(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        """Item 66 round-3 (Opus review, point 7): the source preparer bounds
        every loudness probe to a window instead of the whole file --
        ``probe_start_seconds`` must seek (``-ss`` before ``-i``) and
        ``probe_duration_seconds`` must limit (``-t`` after ``-i``), the
        same convention ``build_conform_source_args`` uses."""
        loudness_module = import_module("civiccast.stream.loudness")
        media = tmp_path / "meeting.wav"
        media.write_bytes(b"RIFFplaceholder")

        captured: dict[str, list[str]] = {}

        def _fake_run_ffmpeg(args: list[str]):
            captured["args"] = args
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
                },
            )()

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(loudness_module, "run_ffmpeg", _fake_run_ffmpeg)

        loudness_module.check_streaming_loudness(
            media_path=media,
            target_lufs=-16.0,
            tolerance_lufs=1.0,
            probe_start_seconds=5.0,
            probe_duration_seconds=30.0,
        )

        args = captured["args"]
        assert args[args.index("-ss") : args.index("-ss") + 2] == ["-ss", "5"]
        assert args.index("-ss") < args.index("-i")  # fast seek, before -i
        assert args[args.index("-t") : args.index("-t") + 2] == ["-t", "30"]
        assert args.index("-t") > args.index("-i")

    def test_no_probe_window_measures_the_whole_file(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        """Companion: leaving both ``probe_start_seconds``/
        ``probe_duration_seconds`` at their default ``None`` (every caller
        except the source preparer) must not add ``-ss``/``-t`` at all --
        the whole-file measurement every other caller relies on is
        unchanged."""
        loudness_module = import_module("civiccast.stream.loudness")
        media = tmp_path / "meeting.wav"
        media.write_bytes(b"RIFFplaceholder")

        captured: dict[str, list[str]] = {}

        def _fake_run_ffmpeg(args: list[str]):
            captured["args"] = args
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
                },
            )()

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(loudness_module, "run_ffmpeg", _fake_run_ffmpeg)

        loudness_module.check_streaming_loudness(
            media_path=media,
            target_lufs=-16.0,
            tolerance_lufs=1.0,
        )

        args = captured["args"]
        assert "-ss" not in args
        assert "-t" not in args

    def test_threads_is_an_input_option_not_an_output_option(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        """Item 66 round-5 (Opus review, point 4): ``-threads`` must come
        BEFORE ``-i`` -- ffmpeg parses an option's position as which
        stream/context it applies to, and an option placed after ``-i``
        (and after ``-t``, in the round-4 shape this replaces) is parsed as
        an OUTPUT option, applying only to the ``-f null`` output rather
        than capping the actual decode. ``-filter_complex_threads`` must
        also be present to cap the ``ebur128`` filter graph's own
        threading, which ``-threads`` alone does not bound."""
        loudness_module = import_module("civiccast.stream.loudness")
        media = tmp_path / "meeting.wav"
        media.write_bytes(b"RIFFplaceholder")

        captured: dict[str, list[str]] = {}

        def _fake_run_ffmpeg(args: list[str]):
            captured["args"] = args
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
                },
            )()

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(loudness_module, "run_ffmpeg", _fake_run_ffmpeg)

        loudness_module.check_streaming_loudness(
            media_path=media,
            target_lufs=-16.0,
            tolerance_lufs=1.0,
            probe_start_seconds=5.0,
            probe_duration_seconds=30.0,
            threads=3,
        )

        args = captured["args"]
        assert args[args.index("-threads") : args.index("-threads") + 2] == ["-threads", "3"]
        assert args.index("-threads") < args.index("-i")  # input/decoder option, not output
        assert args[
            args.index("-filter_complex_threads") : args.index("-filter_complex_threads") + 2
        ] == ["-filter_complex_threads", "3"]
        assert args.index("-filter_complex_threads") < args.index("-i")

    def test_no_threads_omits_both_thread_flags(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        """Companion: the default (``threads=None``, every current caller)
        must not add either flag -- ffmpeg's own default threading is
        unchanged."""
        loudness_module = import_module("civiccast.stream.loudness")
        media = tmp_path / "meeting.wav"
        media.write_bytes(b"RIFFplaceholder")

        captured: dict[str, list[str]] = {}

        def _fake_run_ffmpeg(args: list[str]):
            captured["args"] = args
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
                },
            )()

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(loudness_module, "run_ffmpeg", _fake_run_ffmpeg)

        loudness_module.check_streaming_loudness(
            media_path=media,
            target_lufs=-16.0,
            tolerance_lufs=1.0,
        )

        args = captured["args"]
        assert "-threads" not in args
        assert "-filter_complex_threads" not in args


class TestCheckLoudnessGeneralized:
    """S11b: check_loudness parameterises the standard label + target so cable
    (ATSC A/85 -24 LKFS) and streaming (-16 LUFS) report their own regime."""

    def test_custom_standard_label_and_target_drive_the_report(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        loudness_module = import_module("civiccast.stream.loudness")
        media = tmp_path / "show.wav"
        media.write_bytes(b"RIFFplaceholder")

        monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(
            loudness_module,
            "run_ffmpeg",
            lambda _args: type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "Integrated loudness:\n    I:         -10.0 LUFS\n",
                },
            )(),
        )

        result = loudness_module.check_loudness(
            media_path=media,
            target_lufs=-24.0,
            tolerance_lufs=2.0,
            standard_label="ATSC A/85 -24 LKFS (CALM Act)",
        )

        # -10 LUFS is well outside the -24 target tolerance.
        assert result.status == "failed"
        assert result.standard == "ATSC A/85 -24 LKFS (CALM Act)"
        assert result.target_lufs == -24.0
        # The remediation names the actual target (no hardcoded -16 / "stream").
        assert "-24 LUFS" in result.operator_action
        assert "stream" not in result.operator_action.lower()


def test_the_preparer_loudness_probe_takes_the_preparation_cpu_rung(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """rc.8 MSI defect 1 (2026-11-21): a preparation probe is preparation work.

    The source preparer's loudness probe used to call ``run_ffmpeg`` with no
    priority and no affinity at all, so it decoded its window on every logical
    processor at the live caption child's own NORMAL class -- beside the ride's
    decoder and sink and the program mux. It now takes the rung its caller
    names: ``half_cores`` on the air path (BELOW_NORMAL plus the air-path half,
    exactly what ``run_ffmpeg(half_cores=True)`` gives the conform and the mux)
    and ``lower_priority`` for a background warm. Anything the caller did not
    ask for is not forwarded, so every other caller is byte-for-byte unchanged.
    """

    loudness_module = import_module("civiccast.stream.loudness")
    media = tmp_path / "meeting.wav"
    media.write_bytes(b"RIFFplaceholder")
    forwarded: list[dict[str, object]] = []

    def _fake_run_ffmpeg(_args: list[str], **kwargs: object):
        forwarded.append(kwargs)
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": "",
                "stderr": "Integrated loudness:\n    I:         -16.2 LUFS\n",
            },
        )()

    monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
    monkeypatch.setattr(loudness_module, "run_ffmpeg", _fake_run_ffmpeg)

    loudness_module.check_streaming_loudness(
        media_path=media,
        target_lufs=-16.0,
        tolerance_lufs=1.0,
        half_cores=True,
    )
    loudness_module.check_streaming_loudness(
        media_path=media,
        target_lufs=-16.0,
        tolerance_lufs=1.0,
        lower_priority=True,
    )
    loudness_module.check_streaming_loudness(
        media_path=media,
        target_lufs=-16.0,
        tolerance_lufs=1.0,
    )

    assert forwarded[0] == {"half_cores": True}
    assert forwarded[1] == {"lower_priority": True}
    # No rung named -> the one-argument call every other caller always made.
    assert forwarded[2] == {}
