# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Unit + integration tests for civiccast.schedule.ingest.

Organised into four classes:

  TestFfprobeResultParsing     — parse known JSON dicts into FfprobeResult
  TestValidateIngest           — validation gate accept/reject behaviour
  TestCheckFfprobe             — doctor check with mocked subprocess
  TestRunFfprobeIntegration    — real ffprobe on lavfi test source (Docker-skip
                                 is NOT applied; real-ffprobe tests skip when
                                 the binary is absent, not when Docker is absent)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from civiccast.schedule.ingest import (
    FfmpegNotFoundError,
    FfprobeError,
    FfprobeNotFoundError,
    FfprobeResult,
    UnsafeMediaError,
    UnsupportedFormatError,
    _parse_ffprobe_json,
    check_ffprobe,
    extract_thumbnail,
    hash_file,
    run_ffprobe,
    validate_ingest,
)
from civiccast.stream._ffmpeg import resolve_h264_encoder

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_H264_MP4_JSON: dict = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "duration": "60.0",
        },
        {
            "codec_type": "audio",
            "codec_name": "aac",
        },
    ],
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "60.0",
        "bit_rate": "5000000",
    },
}

_HEVC_MKV_JSON: dict = {
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160},
    ],
    "format": {
        "format_name": "matroska,webm",
        "duration": "3661.2",
        "bit_rate": "20000000",
    },
}

_NO_VIDEO_JSON: dict = {
    "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
    "format": {"format_name": "mp3"},
}

_UNKNOWN_CODEC_JSON: dict = {
    "streams": [{"codec_type": "video", "codec_name": "wmv2", "width": 640, "height": 480}],
    "format": {"format_name": "asf", "duration": "30.0"},
}

_UNKNOWN_AUDIO_JSON: dict = {
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
        {"codec_type": "audio", "codec_name": "wma"},
    ],
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "30.0"},
}


# ---------------------------------------------------------------------------
# TestFfprobeResultParsing
# ---------------------------------------------------------------------------


class TestFfprobeResultParsing:
    """_parse_ffprobe_json extracts correctly typed fields from known JSON."""

    def test_h264_mp4_all_fields(self) -> None:
        result = _parse_ffprobe_json(_H264_MP4_JSON)
        assert result.codec_video == "h264"
        assert result.codec_audio == "aac"
        assert result.width_px == 1920
        assert result.height_px == 1080
        assert result.duration_seconds == 60
        assert result.bitrate_bps == 5_000_000
        assert result.format_name == "mov,mp4,m4a,3gp,3g2,mj2"

    def test_hevc_mkv_no_audio(self) -> None:
        result = _parse_ffprobe_json(_HEVC_MKV_JSON)
        assert result.codec_video == "hevc"
        assert result.codec_audio is None
        assert result.duration_seconds == 3661  # floored from 3661.2
        assert result.width_px == 3840

    def test_no_video_stream(self) -> None:
        result = _parse_ffprobe_json(_NO_VIDEO_JSON)
        assert result.codec_video is None
        assert result.codec_audio == "mp3"
        assert result.width_px is None
        assert result.height_px is None

    def test_missing_bitrate_returns_none(self) -> None:
        data = {
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
            "format": {"format_name": "mp4"},
        }
        result = _parse_ffprobe_json(data)
        assert result.bitrate_bps is None

    def test_duration_falls_back_to_video_stream(self) -> None:
        data = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "duration": "45.5"},
            ],
            "format": {"format_name": "mp4"},  # no duration in format
        }
        result = _parse_ffprobe_json(data)
        assert result.duration_seconds == 45

    def test_raw_field_carries_full_dict(self) -> None:
        result = _parse_ffprobe_json(_H264_MP4_JSON)
        assert result.raw is _H264_MP4_JSON

    def test_empty_streams_and_format(self) -> None:
        result = _parse_ffprobe_json({})
        assert result.codec_video is None
        assert result.codec_audio is None
        assert result.duration_seconds is None
        assert result.bitrate_bps is None
        assert result.format_name is None


# ---------------------------------------------------------------------------
# TestValidateIngest
# ---------------------------------------------------------------------------


class TestValidateIngest:
    """validate_ingest accepts known-good and rejects known-bad assets."""

    def _result(self, data: dict) -> FfprobeResult:
        return _parse_ffprobe_json(data)

    def test_h264_mp4_passes(self) -> None:
        validate_ingest(self._result(_H264_MP4_JSON))  # must not raise

    def test_hevc_mkv_passes(self) -> None:
        validate_ingest(self._result(_HEVC_MKV_JSON))  # must not raise

    def test_no_video_stream_rejected(self) -> None:
        with pytest.raises(UnsupportedFormatError, match="No video stream"):
            validate_ingest(self._result(_NO_VIDEO_JSON))

    def test_unknown_video_codec_rejected(self) -> None:
        with pytest.raises(UnsupportedFormatError, match="wmv2"):
            validate_ingest(self._result(_UNKNOWN_CODEC_JSON))

    def test_unknown_audio_codec_rejected(self) -> None:
        with pytest.raises(UnsupportedFormatError, match="wma"):
            validate_ingest(self._result(_UNKNOWN_AUDIO_JSON))

    def test_unknown_format_rejected(self) -> None:
        result = FfprobeResult(
            duration_seconds=30,
            codec_video="h264",
            codec_audio="aac",
            width_px=1280,
            height_px=720,
            bitrate_bps=None,
            format_name="realvideo",
        )
        with pytest.raises(UnsupportedFormatError, match="realvideo"):
            validate_ingest(result)

    def test_null_format_name_rejected(self) -> None:
        result = FfprobeResult(
            duration_seconds=None,
            codec_video="h264",
            codec_audio=None,
            width_px=None,
            height_px=None,
            bitrate_bps=None,
            format_name=None,
        )
        with pytest.raises(UnsupportedFormatError, match="format"):
            validate_ingest(result)

    def test_vp9_webm_passes(self) -> None:
        result = FfprobeResult(
            duration_seconds=120,
            codec_video="vp9",
            codec_audio="opus",
            width_px=1920,
            height_px=1080,
            bitrate_bps=2_000_000,
            format_name="matroska,webm",
        )
        validate_ingest(result)  # must not raise

    def test_no_audio_codec_still_passes(self) -> None:
        """Video-only files (no audio stream) should pass validation."""
        result = FfprobeResult(
            duration_seconds=30,
            codec_video="h264",
            codec_audio=None,
            width_px=1280,
            height_px=720,
            bitrate_bps=1_000_000,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
        )
        validate_ingest(result)  # must not raise


# ---------------------------------------------------------------------------
# TestCheckFfprobe
# ---------------------------------------------------------------------------


class TestCheckFfprobe:
    """check_ffprobe() reports version correctly; doctor can consume it."""

    def test_returns_none_when_not_on_path(self) -> None:
        with patch("civiccast.schedule.ingest.shutil.which", return_value=None):
            assert check_ffprobe() is None

    def test_returns_version_and_supported_flag(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ffprobe version 6.1.1 Copyright (c) 2007-2024 the FFmpeg developers"
        mock_result.stderr = ""
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.schedule.ingest.subprocess.run", return_value=mock_result),
        ):
            result = check_ffprobe()
        assert result is not None
        version_str, is_supported = result
        assert version_str == "6.1.1"
        assert is_supported is True

    def test_returns_unknown_when_version_unparseable(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ffprobe built from source"
        mock_result.stderr = ""
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.schedule.ingest.subprocess.run", return_value=mock_result),
        ):
            result = check_ffprobe()
        assert result is not None
        version_str, is_supported = result
        assert version_str == "unknown"
        assert is_supported is True  # unknown version treated as supported

    def test_returns_none_on_nonzero_exit(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value="/usr/bin/ffprobe"),
            patch("civiccast.schedule.ingest.subprocess.run", return_value=mock_result),
        ):
            assert check_ffprobe() is None


# ---------------------------------------------------------------------------
# TestRunFfprobeIntegration
# ---------------------------------------------------------------------------

_FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None
_FFPROBE_SKIP = pytest.mark.skipif(
    not _FFPROBE_AVAILABLE,
    reason="ffprobe not on PATH; integration test skipped",
)


class TestRunFfprobeIntegration:
    """Real ffprobe on a lavfi-generated test source (requires ffprobe on PATH)."""

    @_FFPROBE_SKIP
    def test_run_ffprobe_on_generated_video(self, tmp_path: Path) -> None:
        """Generate a short H.264 video with ffmpeg and probe it with ffprobe.

        Uses lavfi testsrc2 (video-only) to avoid audio-filter portability
        issues across ffmpeg builds (aevalsrc is not available on all builds).
        A video-only file is a valid CivicCast asset.
        """
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg required to generate test video")

        import subprocess as sp

        video_path = tmp_path / "test.mp4"
        sp.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=duration=3:size=320x240:rate=25",
                "-c:v",
                resolve_h264_encoder(),
                str(video_path),
            ],
            capture_output=True,
            check=True,
        )

        result = run_ffprobe(video_path)

        assert result.codec_video == "h264"
        assert result.width_px == 320
        assert result.height_px == 240
        assert result.duration_seconds == 3
        assert result.format_name is not None
        assert result.bitrate_bps is not None
        # must also pass the validation gate
        validate_ingest(result)

    @_FFPROBE_SKIP
    def test_run_ffprobe_raises_on_nonexistent_file(self, tmp_path: Path) -> None:
        with pytest.raises(FfprobeError):
            run_ffprobe(tmp_path / "nonexistent.mp4")

    def test_run_ffprobe_raises_when_binary_missing(self, tmp_path: Path) -> None:
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value=None),
            pytest.raises(FfprobeNotFoundError),
        ):
            run_ffprobe(tmp_path / "any.mp4")


# ---------------------------------------------------------------------------
# TestHashFileAndThumbnail (4.0 media-library-hardening)
# ---------------------------------------------------------------------------

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
_FFMPEG_SKIP = pytest.mark.skipif(
    not _FFMPEG_AVAILABLE,
    reason="ffmpeg not on PATH; integration test skipped",
)


def _generate_test_video(tmp_path: Path, *, name: str = "test.mp4", duration: int = 2) -> Path:
    """Generate a small real H.264 video via ffmpeg lavfi (no fixture files checked in)."""
    import subprocess as sp

    video_path = tmp_path / name
    sp.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=duration={duration}:size=320x240:rate=10",
            "-c:v",
            resolve_h264_encoder(),
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        capture_output=True,
        check=True,
    )
    return video_path


class TestHashFile:
    def test_hash_is_sha256_prefixed_and_deterministic(self, tmp_path: Path) -> None:
        path = tmp_path / "a.bin"
        path.write_bytes(b"civiccast test content")

        digest = hash_file(path)

        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64
        assert digest == hash_file(path)  # deterministic

    def test_different_content_hashes_differently(self, tmp_path: Path) -> None:
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"content one")
        b.write_bytes(b"content two")

        assert hash_file(a) != hash_file(b)

    def test_matches_records_domain_digest_format(self, tmp_path: Path) -> None:
        """Same ``sha256:<hex>`` shape as civiccast.records' _digest helper."""
        import hashlib

        path = tmp_path / "a.bin"
        payload = b"shared digest format"
        path.write_bytes(payload)

        assert hash_file(path) == f"sha256:{hashlib.sha256(payload).hexdigest()}"

    def test_streams_large_file_without_loading_fully(self, tmp_path: Path) -> None:
        """A file larger than one hash chunk still hashes correctly (streaming)."""
        import hashlib

        path = tmp_path / "big.bin"
        payload = (b"x" * 1024) * 3000  # ~3MB, several chunks at the 1MB chunk size
        path.write_bytes(payload)

        assert hash_file(path) == f"sha256:{hashlib.sha256(payload).hexdigest()}"


class TestExtractThumbnail:
    @_FFMPEG_SKIP
    def test_extracts_a_real_jpeg_frame(self, tmp_path: Path) -> None:
        video_path = _generate_test_video(tmp_path)
        thumbnail_path = tmp_path / "thumb.jpg"

        extract_thumbnail(video_path, thumbnail_path, at_seconds=0.5)

        assert thumbnail_path.is_file()
        assert thumbnail_path.stat().st_size > 0
        # JPEG magic bytes
        assert thumbnail_path.read_bytes()[:2] == b"\xff\xd8"

    @_FFMPEG_SKIP
    def test_seek_past_duration_falls_back_to_frame_zero(self, tmp_path: Path) -> None:
        """at_seconds beyond the video's duration still produces a frame.

        ffmpeg's fast ``-ss`` seek does not clamp to the last frame — it
        fails outright when the seek target is past EOF. extract_thumbnail
        retries once at frame 0 so short clips (or a default seek point
        that happens to exceed a very short asset's duration) still get a
        thumbnail instead of an error.
        """
        video_path = _generate_test_video(tmp_path, duration=1)
        thumbnail_path = tmp_path / "thumb.jpg"

        extract_thumbnail(video_path, thumbnail_path, at_seconds=100.0)

        assert thumbnail_path.is_file()
        assert thumbnail_path.read_bytes()[:2] == b"\xff\xd8"

    def test_raises_when_ffmpeg_missing(self, tmp_path: Path) -> None:
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value=None),
            pytest.raises(FfmpegNotFoundError),
        ):
            extract_thumbnail(tmp_path / "any.mp4", tmp_path / "thumb.jpg")

    @_FFMPEG_SKIP
    def test_raises_ffprobe_error_on_corrupt_source(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "corrupt.mp4"
        bad_path.write_bytes(b"not a real video file")

        with pytest.raises(FfprobeError):
            extract_thumbnail(bad_path, tmp_path / "thumb.jpg")


# ---------------------------------------------------------------------------
# XML documents are refused before ffprobe runs (CVE-2026-120210, IMF demuxer)
# ---------------------------------------------------------------------------

_IMF_CPL = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/2067-3/2016">'
    "<ContentTitle>x</ContentTitle></CompositionPlaylist>\n"
)

_XML_STARTS = {
    "utf8-decl": _IMF_CPL.encode("utf-8"),
    "utf8-bom": b"\xef\xbb\xbf" + _IMF_CPL.encode("utf-8"),
    "utf8-no-decl-leading-ws": b"\r\n\t  " + _IMF_CPL.split("\n", 1)[1].encode("utf-8"),
    "utf8-comment-first": b"<!-- x -->" + _IMF_CPL.split("\n", 1)[1].encode("utf-8"),
    "utf16le-bom": _IMF_CPL.encode("utf-16"),
    "utf16be-no-bom": _IMF_CPL.encode("utf-16-be"),
    "utf32le-bom": _IMF_CPL.encode("utf-32"),
    "utf32be-no-bom": _IMF_CPL.encode("utf-32-be"),
    "ebcdic": _IMF_CPL.encode("cp500"),
    "ucs4-2143": b"\x00\x00\x3c\x00" + b"\x00" * 12,
    "ucs4-3412": b"\x00\x3c\x00\x00" + b"\x00" * 12,
    "whitespace-padded-past-window": b" " * (70 * 1024) + _IMF_CPL.encode("utf-8"),
}

_BINARY_STARTS = {
    # 60-byte ftyp box: first non-zero byte is "<" (0x3C) in UTF-32-BE terms.
    "mp4-ftyp-60": b"\x00\x00\x00\x3cftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 64,
    "mp4-mdat-first-near-1gb": b"\x3cAB\x10mdat\x00\x00\x00\x01\x67\x64\x00\x1f" + b"\x00" * 64,
    "matroska": b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01" + b"\x00" * 64,
    "mpegts": (b"\x47\x40\x00\x10" + b"\x00" * 184) * 4,
    "avi": b"RIFF\x24\x10\x00\x00AVI LIST" + b"\x00" * 64,
    "all-zero": b"\x00" * 4096,
    "empty": b"",
}


class TestXmlDocumentRefusal:
    @pytest.mark.parametrize("name", sorted(_XML_STARTS))
    def test_xml_documents_are_refused_before_ffprobe(self, name: str, tmp_path: Path) -> None:
        path = tmp_path / "meeting.mp4"
        path.write_bytes(_XML_STARTS[name])
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value="ffprobe"),
            patch("civiccast.schedule.ingest.subprocess.run") as run,
            pytest.raises(UnsafeMediaError) as excinfo,
        ):
            run_ffprobe(path)
        run.assert_not_called()
        # Not an FfprobeError, so the contribution broken-media override
        # (which only catches FfprobeError) can never ingest past it.
        assert not isinstance(excinfo.value, FfprobeError)
        assert isinstance(excinfo.value, UnsupportedFormatError)

    @pytest.mark.parametrize("name", sorted(_BINARY_STARTS))
    def test_binary_containers_still_reach_ffprobe(self, name: str, tmp_path: Path) -> None:
        path = tmp_path / "meeting.mp4"
        path.write_bytes(_BINARY_STARTS[name])
        completed = MagicMock(returncode=1, stdout="", stderr="")
        with (
            patch("civiccast.schedule.ingest.shutil.which", return_value="ffprobe"),
            patch("civiccast.schedule.ingest.subprocess.run", return_value=completed) as run,
            pytest.raises(FfprobeError),
        ):
            run_ffprobe(path)
        run.assert_called_once()
