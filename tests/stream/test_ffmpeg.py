# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for civiccast.stream._ffmpeg — subprocess wrapper and version check."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import civiccast.stream._ffmpeg as ffmpeg_module
from civiccast.stream._ffmpeg import (
    FfmpegCancelledError,
    FfmpegNotFoundError,
    FfmpegResult,
    _parse_ffmpeg_version,
    _version_is_supported,
    check_ffmpeg,
    probe_has_decodable_stream,
    probe_media_duration_seconds,
    run_ffmpeg,
    start_ffmpeg,
)


def _finished_process(pid: int = 1234) -> MagicMock:
    """A Popen stand-in that has already exited cleanly."""
    process = MagicMock(pid=pid, returncode=0)
    process.communicate.return_value = ("", "")
    return process


class _FakePsutil:
    """Records the cores each child pid is pinned to (no real processes)."""

    def __init__(self, own_cores: list[int], *, fail_pin: bool = False) -> None:
        self._own = own_cores
        self._fail = fail_pin
        self.pinned: dict[int, list[int]] = {}

    def Process(self, pid: int | None = None) -> SimpleNamespace:  # psutil's API name
        def cpu_affinity(cpus: list[int] | None = None) -> list[int] | None:
            if cpus is None:
                return list(self._own)
            if self._fail:
                raise OSError("access denied")
            assert pid is not None
            self.pinned[pid] = list(cpus)
            return None

        return SimpleNamespace(cpu_affinity=cpu_affinity)


@pytest.mark.parametrize(
    "changed",
    [
        {},
        {"width": 1920},
        {"avg_frame_rate": "2997/100"},
        {"field_order": "tt"},
        {"sample_aspect_ratio": "4:3"},
        {"pix_fmt": "yuv420p10le"},
        {"side_data_list": [{"rotation": 90}]},
    ],
)
def test_canonical_video_probe_only_accepts_unfiltered_matching_video(monkeypatch, changed):
    stream = {
        "codec_name": "h264",
        "width": 640,
        "height": 360,
        "pix_fmt": "yuv420p",
        "sample_aspect_ratio": "1:1",
        "field_order": "progressive",
        "r_frame_rate": "30/1",
        "avg_frame_rate": "30/1",
    }
    stream.update(changed)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [stream]})),
    )
    assert ffmpeg_module.probe_canonical_video(
        Path("source.mp4"), width=640, height=360, fps=30
    ) is (not changed)


def test_run_ffmpeg_cancel_event_terminates_owned_process(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel_event = threading.Event()
    process = MagicMock()
    process.returncode = -15

    def communicate(*, timeout: float | None = None):  # type: ignore[no-untyped-def]
        if not cancel_event.is_set():
            cancel_event.set()
            raise subprocess.TimeoutExpired(["ffmpeg"], timeout)
        return ("", "")

    process.communicate.side_effect = communicate
    monkeypatch.setattr(ffmpeg_module, "_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_module, "_resolve_video_encoder_args", lambda args, _path: args)
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", popen)

    with pytest.raises(FfmpegCancelledError, match="cancelled"):
        run_ffmpeg(["-version"], cancel_event=cancel_event)

    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()


def test_run_ffmpeg_pre_cancel_does_not_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    resolve_path = MagicMock(side_effect=AssertionError("cancelled work resolved ffmpeg"))
    popen = MagicMock()
    monkeypatch.setattr(ffmpeg_module, "_ffmpeg_path", resolve_path)
    monkeypatch.setattr(subprocess, "Popen", popen)

    with pytest.raises(FfmpegCancelledError, match="before launch"):
        run_ffmpeg(["-version"], cancel_event=cancel_event)

    resolve_path.assert_not_called()
    popen.assert_not_called()


def test_run_ffmpeg_cancel_during_resolution_does_not_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = threading.Event()
    popen = MagicMock()

    def cancel_during_resolution(args: list[str], _path: str) -> list[str]:
        cancel_event.set()
        return args

    monkeypatch.setattr(ffmpeg_module, "_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg_module, "_resolve_video_encoder_args", cancel_during_resolution)
    monkeypatch.setattr(subprocess, "Popen", popen)

    with pytest.raises(FfmpegCancelledError, match="before launch"):
        run_ffmpeg(["-version"], cancel_event=cancel_event)

    popen.assert_not_called()


def _all_usable(_path: str, _encoder: str) -> bool:
    return True


class TestH264EncoderResolution:
    def test_prefers_nvenc_when_all_supported_encoders_are_present(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        assert (
            resolver(
                ffmpeg_path="C:/ffmpeg/all/ffmpeg.exe",
                probe=lambda _path: {"h264_nvenc", "h264_mf", "libopenh264"},
                verify=_all_usable,
            )
            == "h264_nvenc"
        )

    def test_uses_media_foundation_when_nvenc_is_absent(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        assert (
            resolver(
                ffmpeg_path="C:/ffmpeg/mf/ffmpeg.exe",
                probe=lambda _path: {"h264_mf", "libopenh264"},
                verify=_all_usable,
            )
            == "h264_mf"
        )

    def test_uses_openh264_when_it_is_the_only_supported_encoder(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        assert (
            resolver(
                ffmpeg_path="C:/ffmpeg/openh264/ffmpeg.exe",
                probe=lambda _path: {"libopenh264"},
                verify=_all_usable,
            )
            == "libopenh264"
        )

    def test_advertised_but_unusable_nvenc_falls_through_to_a_usable_encoder(self) -> None:
        """The CI-caught defect: h264_nvenc listed by -encoders but unable to
        initialize (no NVIDIA runtime) must not be selected. Advertised is not
        usable; the resolver verifies each candidate and falls through."""
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        assert (
            resolver(
                ffmpeg_path="C:/ffmpeg/bare-nvenc/ffmpeg.exe",
                probe=lambda _path: {"h264_nvenc", "libopenh264"},
                verify=lambda _path, encoder: encoder != "h264_nvenc",
            )
            == "libopenh264"
        )

    def test_libx264_is_the_last_resort_when_it_is_the_only_usable_encoder(self) -> None:
        """A station running a full GPL ffmpeg build (WSL line, distro ffmpeg,
        CI) must keep encoding. The pinned LGPL pack never carries libx264, so
        native-line resolution can never reach this branch."""
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        assert (
            resolver(
                ffmpeg_path="C:/ffmpeg/gpl-only/ffmpeg.exe",
                probe=lambda _path: {"libx264"},
                verify=_all_usable,
            )
            == "libx264"
        )

    def test_prefers_royalty_free_openh264_over_libx264(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        assert (
            resolver(
                ffmpeg_path="C:/ffmpeg/soft-both/ffmpeg.exe",
                probe=lambda _path: {"libopenh264", "libx264"},
                verify=_all_usable,
            )
            == "libopenh264"
        )

    def test_refuses_when_no_supported_h264_encoder_is_present(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        error_type = getattr(ffmpeg_module, "H264EncoderUnavailableError", RuntimeError)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        with pytest.raises(error_type) as caught:
            resolver(
                ffmpeg_path="C:/ffmpeg/none/ffmpeg.exe",
                probe=lambda _path: {"aac", "mpeg2video"},
                verify=_all_usable,
            )

        message = str(caught.value)
        assert "aac" in message
        assert "mpeg2video" in message
        assert "h264_nvenc" in message
        assert "h264_mf" in message
        assert "libopenh264" in message

    def test_refuses_loudly_when_every_advertised_encoder_fails_usability(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        error_type = getattr(ffmpeg_module, "H264EncoderUnavailableError", RuntimeError)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"

        with pytest.raises(error_type) as caught:
            resolver(
                ffmpeg_path="C:/ffmpeg/all-broken/ffmpeg.exe",
                probe=lambda _path: {"h264_nvenc", "libopenh264"},
                verify=lambda _path, _encoder: False,
            )

        message = str(caught.value)
        assert "h264_nvenc" in message
        assert "libopenh264" in message
        assert "usability" in message or "failed" in message

    def test_probe_and_usability_are_cached_per_binary_path(self) -> None:
        resolver = getattr(ffmpeg_module, "resolve_h264_encoder", None)
        assert resolver is not None, "the ffmpeg H.264 resolver must exist"
        probe_calls: list[str] = []
        verify_calls: list[str] = []

        def probe(path: str) -> set[str]:
            probe_calls.append(path)
            return {"h264_mf"}

        def verify(path: str, encoder: str) -> bool:
            verify_calls.append(encoder)
            return True

        path = "C:/ffmpeg/cache/ffmpeg.exe"
        assert resolver(ffmpeg_path=path, probe=probe, verify=verify) == "h264_mf"
        assert resolver(ffmpeg_path=path, probe=probe, verify=verify) == "h264_mf"
        assert probe_calls == [path]
        assert verify_calls == ["h264_mf"]

    def test_default_usability_check_runs_a_one_frame_null_encode(self) -> None:
        checker = getattr(ffmpeg_module, "verify_h264_encoder_usable", None)
        assert checker is not None, "the usability checker must exist"

        completed = MagicMock(returncode=0, stderr="")
        with patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed) as spawn:
            assert checker("C:/runtime/ffmpeg.exe", "libopenh264") is True

        argv = spawn.call_args.args[0]
        assert argv[0] == "C:/runtime/ffmpeg.exe"
        assert "-c:v" in argv and argv[argv.index("-c:v") + 1] == "libopenh264"
        assert "-frames:v" in argv
        assert argv[-2:] == ["-f", "null"] or "null" in argv

    def test_default_usability_check_reports_encoder_init_failure(self) -> None:
        checker = getattr(ffmpeg_module, "verify_h264_encoder_usable", None)
        assert checker is not None, "the usability checker must exist"

        completed = MagicMock(returncode=255, stderr="Cannot load libcuda.so.1")
        with patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed):
            assert checker("C:/runtime/ffmpeg.exe", "h264_nvenc") is False

    def test_default_usability_check_treats_a_hung_encoder_as_unusable(self) -> None:
        checker = getattr(ffmpeg_module, "verify_h264_encoder_usable", None)
        assert checker is not None, "the usability checker must exist"

        with patch(
            "civiccast.stream._ffmpeg.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=60),
        ):
            assert checker("C:/runtime/ffmpeg.exe", "h264_mf") is False

    @pytest.mark.parametrize(
        ("encoder", "expected_pair"),
        [
            ("h264_mf", None),
            ("libopenh264", ["-profile:v", "constrained_baseline"]),
            ("libx264", ["-profile:v", "baseline"]),
            ("h264_nvenc", ["-profile:v", "baseline"]),
        ],
    )
    def test_profile_option_is_translated_to_the_resolved_encoders_dialect(
        self, encoder: str, expected_pair: list[str] | None
    ) -> None:
        """Live-measured against the pinned pack binary (evidence 12): h264_mf
        rejects every named -profile:v value, libopenh264 only knows
        constrained_baseline. Resolving the encoder NAME without translating
        its options broke slate generation on MF hosts."""
        with patch("civiccast.stream._ffmpeg.resolve_h264_encoder", return_value=encoder):
            resolved = ffmpeg_module._resolve_video_encoder_args(
                ["-c:v", "h264", "-profile:v", "baseline", "-b:v", "800k"],
                "C:/runtime/ffmpeg.exe",
            )

        assert resolved[:2] == ["-c:v", encoder]
        assert resolved[-2:] == ["-b:v", "800k"]
        if expected_pair is None:
            assert "-profile:v" not in resolved
        else:
            assert expected_pair == resolved[2:4]

    def test_profile_translation_leaves_non_h264_args_untouched(self) -> None:
        resolved = ffmpeg_module._resolve_video_encoder_args(
            ["-c:v", "copy", "-profile:v", "baseline"], "C:/runtime/ffmpeg.exe"
        )
        assert resolved == ["-c:v", "copy", "-profile:v", "baseline"]

    def test_stream_qualified_profile_translates_with_its_matching_codec(self) -> None:
        """terra round-3 Major 1: -c:v:0 h264 resolved but -profile:v:0 was
        left in libx264 dialect — the pack's h264_mf rejected the argv."""
        with patch("civiccast.stream._ffmpeg.resolve_h264_encoder", return_value="h264_mf"):
            resolved = ffmpeg_module._resolve_video_encoder_args(
                ["-c:v:0", "h264", "-profile:v:0", "baseline", "-b:v", "800k"],
                "C:/runtime/ffmpeg.exe",
            )
        assert resolved == ["-c:v:0", "h264_mf", "-b:v", "800k"]

    def test_profile_for_a_stream_we_did_not_resolve_passes_through(self) -> None:
        with patch("civiccast.stream._ffmpeg.resolve_h264_encoder", return_value="h264_mf"):
            resolved = ffmpeg_module._resolve_video_encoder_args(
                ["-c:v:0", "h264", "-profile:v:1", "high"],
                "C:/runtime/ffmpeg.exe",
            )
        assert resolved == ["-c:v:0", "h264_mf", "-profile:v:1", "high"]

    def test_unqualified_profile_does_not_pair_with_a_qualified_codec(self) -> None:
        with patch("civiccast.stream._ffmpeg.resolve_h264_encoder", return_value="h264_mf"):
            resolved = ffmpeg_module._resolve_video_encoder_args(
                ["-c:v:0", "h264", "-profile:v", "baseline"],
                "C:/runtime/ffmpeg.exe",
            )
        assert resolved == ["-c:v:0", "h264_mf", "-profile:v", "baseline"]

    def test_default_usability_check_treats_a_spawn_failure_as_unusable(self) -> None:
        checker = getattr(ffmpeg_module, "verify_h264_encoder_usable", None)
        assert checker is not None, "the usability checker must exist"

        with patch(
            "civiccast.stream._ffmpeg.subprocess.run",
            side_effect=OSError("cannot execute binary"),
        ):
            assert checker("C:/runtime/ffmpeg.exe", "libopenh264") is False


class TestVersionParsing:
    def test_parses_standard_version(self) -> None:
        output = "ffmpeg version 4.4.2 Copyright (c) 2000-2021 the FFmpeg developers"
        assert _parse_ffmpeg_version(output) == "4.4.2"

    def test_parses_n_prefixed_version(self) -> None:
        output = "ffmpeg version n6.1.1 Copyright (c) 2000-2024 the FFmpeg developers"
        assert _parse_ffmpeg_version(output) == "n6.1.1"

    def test_parses_git_build_version(self) -> None:
        output = "ffmpeg version N-107442-g5f05cb48e8 Copyright ..."
        assert _parse_ffmpeg_version(output) is not None

    def test_returns_none_for_empty_output(self) -> None:
        assert _parse_ffmpeg_version("") is None

    def test_case_insensitive(self) -> None:
        output = "FFmpeg version 5.0 ..."
        assert _parse_ffmpeg_version(output) == "5.0"


class TestVersionSupported:
    def test_4_4_is_supported(self) -> None:
        assert _version_is_supported("4.4") is True

    def test_4_4_2_is_supported(self) -> None:
        assert _version_is_supported("4.4.2") is True

    def test_6_0_is_supported(self) -> None:
        assert _version_is_supported("6.0") is True

    def test_4_3_is_not_supported(self) -> None:
        assert _version_is_supported("4.3") is False

    def test_3_4_is_not_supported(self) -> None:
        assert _version_is_supported("3.4") is False

    def test_n_prefix_stripped(self) -> None:
        assert _version_is_supported("n6.1.1") is True

    def test_build_metadata_ignored(self) -> None:
        assert _version_is_supported("4.4.2-0ubuntu1") is True

    def test_unknown_format_is_treated_as_supported(self) -> None:
        # Don't block on unrecognised version strings.
        assert _version_is_supported("unknown-build") is True


class TestRunFfmpeg:
    def test_raises_ffmpeg_not_found_when_binary_absent(self) -> None:
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value=None),
            pytest.raises(FfmpegNotFoundError, match="ffmpeg not found"),
        ):
            run_ffmpeg(["-version"])

    def test_returns_ffmpeg_result_on_success(self) -> None:
        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = "some output"
        mock_completed.stderr = "some stderr"

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed),
        ):
            result = run_ffmpeg(["-version"])

        assert isinstance(result, FfmpegResult)
        assert result.returncode == 0

    def test_returns_nonzero_returncode_on_failure(self) -> None:
        mock_completed = MagicMock()
        mock_completed.returncode = 1
        mock_completed.stdout = ""
        mock_completed.stderr = "Invalid option"

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed),
        ):
            result = run_ffmpeg(["-invalid-flag"])

        assert result.returncode == 1
        assert "Invalid option" in result.stderr

    def test_progress_callback_called_for_each_stderr_line(self) -> None:
        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = ""
        mock_completed.stderr = "line one\nline two\nline three"

        collected: list[str] = []

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed),
        ):
            run_ffmpeg(["-version"], progress_callback=collected.append)

        assert collected == ["line one", "line two", "line three"]

    def test_no_shell_injection_in_command(self) -> None:
        """Verify run_ffmpeg always passes a list (no shell=True)."""
        captured_call: list[object] = []

        def fake_run(cmd: object, **kwargs: object) -> MagicMock:
            captured_call.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", side_effect=fake_run),
        ):
            run_ffmpeg(["-version"])

        assert isinstance(captured_call[0], list)
        # The command must never be a bare string (shell injection risk).
        assert not isinstance(captured_call[0], str)

    @pytest.mark.parametrize("requested", ["h264", "libx264"])
    def test_resolves_h264_request_for_the_exact_spawned_binary(self, requested: str) -> None:
        mock_completed = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch(
                "civiccast.stream._ffmpeg.shutil.which",
                return_value="C:/runtime/ffmpeg.exe",
            ),
            patch(
                "civiccast.stream._ffmpeg.resolve_h264_encoder",
                return_value="h264_mf",
            ) as resolver,
            patch(
                "civiccast.stream._ffmpeg.subprocess.run",
                return_value=mock_completed,
            ) as spawn,
        ):
            run_ffmpeg(["-i", "input.ts", "-c:v", requested, "output.ts"])

        resolver.assert_called_once_with(ffmpeg_path="C:/runtime/ffmpeg.exe")
        assert spawn.call_args.args[0] == [
            "C:/runtime/ffmpeg.exe",
            "-y",
            "-i",
            "input.ts",
            "-c:v",
            "h264_mf",
            "output.ts",
        ]

    def test_default_priority_is_unchanged(self) -> None:
        """lower_priority defaults False -- real-time/latency-sensitive
        callers (live egress, the VOD packager answering an HTTP request)
        must keep running at normal process priority unless they opt in."""
        mock_completed = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed) as spawn,
        ):
            run_ffmpeg(["-version"])

        assert spawn.call_args.kwargs["creationflags"] == 0

    def test_lower_priority_sets_idle_creationflags(self) -> None:
        """FT-7 (RC4 field test, 2026-11-05): background ffmpeg (the warm
        conform of the next programme and the media-lifecycle proxy transcode)
        must sit BELOW the live caption speech child, which Windows starts at
        BELOW_NORMAL. At BELOW_NORMAL these whole-programme encodes tied with
        the captions, out-threaded them, and on the Beelink live captions were
        discarded while every hour-long programme was being prepared."""
        nt_os = SimpleNamespace(name="nt")
        with (
            patch.object(ffmpeg_module, "os", nt_os),
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "civiccast.stream._ffmpeg.subprocess.Popen", return_value=_finished_process()
            ) as spawn,
            patch("civiccast.stream._ffmpeg.psutil", _FakePsutil(list(range(16))), create=True),
        ):
            run_ffmpeg(["-version"], lower_priority=True)

        assert spawn.call_args.kwargs["creationflags"] == 0x40  # IDLE_PRIORITY_CLASS

    def test_background_priority_is_below_the_caption_child(self) -> None:
        """The ordering FT-7 depends on, pinned as numbers so it holds on a
        non-Windows test host too: Windows priority classes rank
        IDLE (0x40) < BELOW_NORMAL (0x4000) < NORMAL (0x20). The caption
        speech child is spawned with BELOW_NORMAL (0x4000, see
        civiccast.captions.whistle); background ffmpeg must use IDLE."""
        rank = {0x40: 4, 0x4000: 6, 0x20: 8}  # Windows base priority per class
        assert ffmpeg_module._BACKGROUND_PRIORITY_CLASS == 0x40
        assert rank[ffmpeg_module._BACKGROUND_PRIORITY_CLASS] < rank[0x4000]

    @pytest.mark.parametrize(
        ("available", "expected"),
        [
            (list(range(4)), None),  # a 4-core box keeps every core
            (list(range(8)), [4, 5, 6, 7]),  # never fewer than 4
            (list(range(16)), [12, 13, 14, 15]),  # the Beelink: a quarter
            (list(range(32)), list(range(24, 32))),
            ([0, 2, 4, 6, 8, 10, 12, 14], [8, 10, 12, 14]),  # an already-restricted parent
        ],
    )
    def test_background_cores_are_the_top_quarter_never_fewer_than_four(
        self, available: list[int], expected: list[int] | None
    ) -> None:
        """FT-7 (measured on the Beelink, 2026-11-06): h264_mf there is the
        Microsoft software H.264 MFT, which threads across every core and
        ignores ``-threads 1``, so IDLE priority alone still left live
        captions 20-33 s late while a warm ran. 4 of 16 cores ran the conform
        at 12.3x (2 cores: 5.9x, too close to the 8x/1.5 warm timeout);
        all 16: 19.2x. With no background encode, captions were 3.3 s."""
        assert ffmpeg_module._background_cores(available) == expected

    def test_windows_background_ffmpeg_is_pinned_to_the_background_cores(self) -> None:
        nt_os = SimpleNamespace(name="nt")
        fake_psutil = _FakePsutil(list(range(16)))
        with (
            patch.object(ffmpeg_module, "os", nt_os),
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "civiccast.stream._ffmpeg.subprocess.Popen",
                return_value=_finished_process(pid=4321),
            ),
            patch("civiccast.stream._ffmpeg.psutil", fake_psutil, create=True),
        ):
            result = run_ffmpeg(["-version"], lower_priority=True)

        assert result.returncode == 0
        assert fake_psutil.pinned == {4321: [12, 13, 14, 15]}

    def test_a_failed_core_pin_never_fails_the_background_job(self) -> None:
        nt_os = SimpleNamespace(name="nt")
        fake_psutil = _FakePsutil(list(range(16)), fail_pin=True)
        with (
            patch.object(ffmpeg_module, "os", nt_os),
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.Popen", return_value=_finished_process()),
            patch("civiccast.stream._ffmpeg.psutil", fake_psutil, create=True),
        ):
            result = run_ffmpeg(["-version"], lower_priority=True)

        assert result.returncode == 0
        assert fake_psutil.pinned == {}

    def test_foreground_ffmpeg_is_never_pinned(self) -> None:
        nt_os = SimpleNamespace(name="nt")
        fake_psutil = _FakePsutil(list(range(16)))
        mock_completed = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch.object(ffmpeg_module, "os", nt_os),
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed),
            patch("civiccast.stream._ffmpeg.psutil", fake_psutil, create=True),
        ):
            run_ffmpeg(["-version"])

        assert fake_psutil.pinned == {}

    def test_lower_priority_prefixes_nice_on_posix(self) -> None:
        """U29 3b: Windows has no ``nice``; the equivalent there is the
        BELOW_NORMAL priority class above.  On a POSIX host the same opt-in
        must lower the process's scheduling priority to ``nice`` 10 -- and it
        must do it by prefixing the argv (``nice`` execs ffmpeg itself) rather
        than ``preexec_fn``, which is unsafe from a thread other than the one
        that called ``subprocess`` and the warm runs on its own worker
        thread."""
        mock_completed = MagicMock(returncode=0, stdout="", stderr="")
        posix_os = SimpleNamespace(name="posix")
        with (
            patch.object(ffmpeg_module, "os", posix_os),
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed) as spawn,
        ):
            run_ffmpeg(["-version"], lower_priority=True)

        assert spawn.call_args.args[0] == [
            "nice",
            "-n",
            "10",
            "/usr/bin/ffmpeg",
            "-y",
            "-version",
        ]
        assert spawn.call_args.kwargs["creationflags"] == 0  # no Windows flag on POSIX

    def test_lower_priority_is_a_noop_without_nice_on_posix(self) -> None:
        """Degrade rather than fail: a POSIX host with no ``nice`` on PATH
        still runs ffmpeg (at normal priority) instead of raising
        FileNotFoundError from the subprocess call."""
        mock_completed = MagicMock(returncode=0, stdout="", stderr="")
        posix_os = SimpleNamespace(name="posix")

        def which(name: str) -> str | None:
            return None if name == "nice" else "/usr/bin/ffmpeg"

        with (
            patch.object(ffmpeg_module, "os", posix_os),
            patch("civiccast.stream._ffmpeg.shutil.which", side_effect=which),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_completed) as spawn,
        ):
            run_ffmpeg(["-version"], lower_priority=True)

        assert spawn.call_args.args[0] == ["/usr/bin/ffmpeg", "-y", "-version"]


@pytest.mark.parametrize("output_stream", ["stdout", "stderr"])
def test_probe_ffmpeg_encoders_uses_exact_binary_and_parses_registered_names(
    output_stream: str,
) -> None:
    output = """Encoders:
 V..... = Video
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder
 V..... libopenh264          OpenH264 H.264 / AVC
 A..... aac                  AAC
"""
    completed = MagicMock(
        returncode=0,
        stdout=output if output_stream == "stdout" else "",
        stderr=output if output_stream == "stderr" else "",
    )
    with patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed) as spawn:
        encoders = ffmpeg_module.probe_ffmpeg_encoders("C:/runtime/ffmpeg.exe")

    assert encoders == {"h264_nvenc", "libopenh264", "aac"}
    assert spawn.call_args.args[0] == [
        "C:/runtime/ffmpeg.exe",
        "-hide_banner",
        "-encoders",
    ]


class TestProbeMediaDurationSeconds:
    """Item 66 round-5 (Opus review, point 2): a cheap ``-format`` query
    (never a decode) for the source preparer's untrimmed loudness-probe
    window, deliberately total like ``probe_video_dimensions``."""

    def test_parses_duration_from_ffprobe_output(self, tmp_path) -> None:
        completed = MagicMock(returncode=0, stdout="3600.048000\n", stderr="")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed) as spawn,
        ):
            duration = probe_media_duration_seconds(tmp_path / "long-recording.mp4")

        assert duration == pytest.approx(3600.048)
        assert spawn.call_args.args[0] == [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(tmp_path / "long-recording.mp4"),
        ]

    def test_returns_none_when_ffprobe_absent(self, tmp_path) -> None:
        with patch("civiccast.stream._ffmpeg.shutil.which", return_value=None):
            assert probe_media_duration_seconds(tmp_path / "asset.mp4") is None

    def test_returns_none_on_nonzero_exit(self, tmp_path) -> None:
        completed = MagicMock(returncode=1, stdout="", stderr="No such file")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed),
        ):
            assert probe_media_duration_seconds(tmp_path / "missing.mp4") is None

    def test_returns_none_on_unparseable_output(self, tmp_path) -> None:
        completed = MagicMock(returncode=0, stdout="N/A\n", stderr="")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed),
        ):
            assert probe_media_duration_seconds(tmp_path / "asset.mp4") is None

    def test_returns_none_on_timeout(self, tmp_path) -> None:
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "civiccast.stream._ffmpeg.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
            ),
        ):
            assert probe_media_duration_seconds(tmp_path / "asset.mp4") is None

    def test_returns_none_for_nonpositive_duration(self, tmp_path) -> None:
        completed = MagicMock(returncode=0, stdout="0\n", stderr="")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed),
        ):
            assert probe_media_duration_seconds(tmp_path / "asset.mp4") is None


class TestProbeHasDecodableStream:
    """U36 item 7: three answers, not two. The distinction matters because a
    caller deciding what to do with a file it just produced must not read
    "ffprobe could not be asked" as "there are no streams in it" — that is what
    ``probe_media_duration_seconds``'s single ``None`` cannot express."""

    def test_reports_true_and_the_stream_listing_query(self, tmp_path) -> None:
        completed = MagicMock(returncode=0, stdout="video\naudio\n", stderr="")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed) as spawn,
        ):
            assert probe_has_decodable_stream(tmp_path / "segment.ts") is True

        assert spawn.call_args.args[0] == [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(tmp_path / "segment.ts"),
        ]

    def test_reports_false_when_the_container_lists_no_stream(self, tmp_path) -> None:
        completed = MagicMock(returncode=0, stdout="\n", stderr="")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed),
        ):
            assert probe_has_decodable_stream(tmp_path / "empty.ts") is False

    def test_reports_false_when_ffprobe_cannot_parse_the_file(self, tmp_path) -> None:
        completed = MagicMock(returncode=1, stdout="", stderr="Invalid data found")
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=completed),
        ):
            assert probe_has_decodable_stream(tmp_path / "truncated.ts") is False

    def test_reports_none_when_ffprobe_is_absent(self, tmp_path) -> None:
        with patch("civiccast.stream._ffmpeg.shutil.which", return_value=None):
            assert probe_has_decodable_stream(tmp_path / "asset.mp4") is None

    def test_reports_none_when_ffprobe_times_out(self, tmp_path) -> None:
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "civiccast.stream._ffmpeg.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
            ),
        ):
            assert probe_has_decodable_stream(tmp_path / "asset.mp4") is None

    def test_reports_none_when_ffprobe_cannot_be_executed(self, tmp_path) -> None:
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "civiccast.stream._ffmpeg.subprocess.run",
                side_effect=OSError("cannot execute"),
            ),
        ):
            assert probe_has_decodable_stream(tmp_path / "asset.mp4") is None


class TestCheckFfmpeg:
    def test_returns_none_when_ffmpeg_not_found(self) -> None:
        with patch("civiccast.stream._ffmpeg.shutil.which", return_value=None):
            assert check_ffmpeg() is None

    def test_returns_version_and_supported_flag(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ffmpeg version 5.1.4 Copyright ..."
        mock_result.stderr = ""

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_result),
        ):
            result = check_ffmpeg()

        assert result is not None
        version, is_supported = result
        assert version == "5.1.4"
        assert is_supported is True

    def test_flags_old_version_as_unsupported(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ffmpeg version 3.4.0 Copyright ..."
        mock_result.stderr = ""

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.run", return_value=mock_result),
        ):
            result = check_ffmpeg()

        assert result is not None
        _, is_supported = result
        assert is_supported is False


class TestStartFfmpeg:
    def test_raises_ffmpeg_not_found_when_binary_absent(self) -> None:
        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value=None),
            pytest.raises(FfmpegNotFoundError, match="ffmpeg not found"),
        ):
            start_ffmpeg(["-version"])

    def test_starts_process_without_shell_and_returns_handle(self) -> None:
        fake_process = MagicMock()
        fake_process.pid = 1234
        fake_process.poll.return_value = None
        captured: dict[str, object] = {}

        def fake_popen(cmd: object, **kwargs: object) -> MagicMock:
            captured["cmd"] = cmd
            captured.update(kwargs)
            return fake_process

        with (
            patch("civiccast.stream._ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("civiccast.stream._ffmpeg.subprocess.Popen", side_effect=fake_popen),
            patch("civiccast.stream._ffmpeg.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            handle = start_ffmpeg(["-version"])

        assert handle.pid == 1234
        assert captured["cmd"] == ["/usr/bin/ffmpeg", "-y", "-version"]
        assert isinstance(captured["cmd"], list)
        assert captured["creationflags"] == 0x08000000
        assert captured["stdout"] is not None
        assert captured["stderr"] is not None

    def test_handle_terminate_waits_then_closes(self) -> None:
        fake_process = MagicMock()
        fake_process.pid = 1234
        fake_process.poll.return_value = None
        fake_process.returncode = 0
        handle = start_handle_for_test(fake_process)

        result = handle.terminate(grace_seconds=0.1)

        fake_process.terminate.assert_called_once()
        fake_process.wait.assert_called_once_with(timeout=0.1)
        assert result == 0


def start_handle_for_test(fake_process: MagicMock):
    from civiccast.stream._ffmpeg import FfmpegProcessHandle

    return FfmpegProcessHandle(process=fake_process)
