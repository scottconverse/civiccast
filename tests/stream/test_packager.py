# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for civiccast.stream.packager — VOD packager logic.

Unit tests mock ffmpeg calls. Integration tests (marked @pytest.mark.integration)
require ffmpeg installed and a real test video; those run in CI with ffmpeg
available and are skipped otherwise.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from civiccast.stream._ffmpeg import FfmpegNotFoundError
from civiccast.stream.config import ABR_LADDER, RenditionConfig
from civiccast.stream.packager import (
    PackagingError,
    SlateOnlyResult,
    VodPackageResult,
    _encode_rendition,
    pack_slate_fallback,
    pack_vod_asset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_slate(output_dir: Path) -> Path:
    """Create a minimal slate playlist on disk without invoking ffmpeg."""
    slate_dir = output_dir / "slate"
    slate_dir.mkdir(parents=True, exist_ok=True)
    playlist = slate_dir / "playlist.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:2.0,\nseg000.ts\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    # Create a stub segment so "playlist exists" checks pass.
    (slate_dir / "seg000.ts").write_bytes(b"\x00" * 16)
    return playlist


def _make_stub_rendition_playlist(output_dir: Path, name: str) -> Path:
    """Create a minimal variant playlist on disk without invoking ffmpeg."""
    rend_dir = output_dir / name
    rend_dir.mkdir(parents=True, exist_ok=True)
    playlist = rend_dir / "playlist.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:2.0,\nseg000.ts\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    (rend_dir / "seg000.ts").write_bytes(b"\x00" * 16)
    return playlist


# ---------------------------------------------------------------------------
# PackagingError
# ---------------------------------------------------------------------------


class TestPackagingError:
    def test_carries_rendition_name(self) -> None:
        err = PackagingError("failed", rendition="720p", ffmpeg_stderr="err")
        assert err.rendition == "720p"
        assert err.ffmpeg_stderr == "err"
        assert "failed" in str(err)

    def test_optional_fields_default_to_none(self) -> None:
        err = PackagingError("oops")
        assert err.rendition is None
        assert err.ffmpeg_stderr is None


# ---------------------------------------------------------------------------
# pack_vod_asset — unit tests (mocked ffmpeg)
# ---------------------------------------------------------------------------


class TestPackVodAssetUnit:
    def _mock_successful_pack(self, tmp_path: Path, input_file: Path) -> VodPackageResult:
        """Simulate a successful pack by creating stub outputs before the packager checks."""

        def fake_generate_slate(output_dir: Path) -> Path:
            return _make_stub_slate(output_dir)

        def fake_encode(
            input_path: Path, output_dir: Path, config: object, **kwargs: object
        ) -> Path:
            from civiccast.stream.config import RenditionConfig

            assert isinstance(config, RenditionConfig)
            return _make_stub_rendition_playlist(output_dir, config.name)

        with (
            patch("civiccast.stream.packager.generate_slate", side_effect=fake_generate_slate),
            patch("civiccast.stream.packager._encode_rendition", side_effect=fake_encode),
        ):
            return pack_vod_asset(input_file, tmp_path / "output")

    def test_raises_file_not_found_for_missing_input(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            pack_vod_asset(tmp_path / "nonexistent.mp4", tmp_path / "out")

    def test_raises_value_error_for_directory_input(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a file"):
            pack_vod_asset(tmp_path, tmp_path / "out")

    def test_returns_vod_package_result_on_success(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        result = self._mock_successful_pack(tmp_path, input_file)
        assert isinstance(result, VodPackageResult)

    def test_manifest_is_written_to_output_dir(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        result = self._mock_successful_pack(tmp_path, input_file)
        assert result.manifest_path.exists()
        assert result.manifest_path.name == "playlist.m3u8"

    def test_result_has_five_renditions(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        result = self._mock_successful_pack(tmp_path, input_file)
        assert len(result.renditions) == 5  # 4 content + slate

    def test_slate_is_last_rendition(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        result = self._mock_successful_pack(tmp_path, input_file)
        assert result.slate.config.name == "slate"
        assert result.renditions[-1].config.name == "slate"

    def test_manifest_contains_all_five_variants(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        result = self._mock_successful_pack(tmp_path, input_file)
        manifest_text = result.manifest_path.read_text(encoding="utf-8")
        assert manifest_text.count("#EXT-X-STREAM-INF:") == 5

    def test_slate_always_generated_before_content(self, tmp_path: Path) -> None:
        """Slate generation happens first — must not be blocked by content failure."""
        call_order: list[str] = []
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)

        def fake_generate_slate(output_dir: Path) -> Path:
            call_order.append("slate")
            return _make_stub_slate(output_dir)

        def fake_encode(
            input_path: Path, output_dir: Path, config: object, **kwargs: object
        ) -> Path:
            from civiccast.stream.config import RenditionConfig

            assert isinstance(config, RenditionConfig)
            call_order.append(f"encode:{config.name}")
            return _make_stub_rendition_playlist(output_dir, config.name)

        with (
            patch("civiccast.stream.packager.generate_slate", side_effect=fake_generate_slate),
            patch("civiccast.stream.packager._encode_rendition", side_effect=fake_encode),
        ):
            pack_vod_asset(input_file, tmp_path / "output")

        assert call_order[0] == "slate"

    def test_propagates_ffmpeg_not_found(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)

        def fake_generate_slate(output_dir: Path) -> Path:
            return _make_stub_slate(output_dir)

        with (
            patch("civiccast.stream.packager.generate_slate", side_effect=fake_generate_slate),
            patch(
                "civiccast.stream.packager._encode_rendition",
                side_effect=FfmpegNotFoundError("no ffmpeg"),
            ),
            pytest.raises(FfmpegNotFoundError),
        ):
            pack_vod_asset(input_file, tmp_path / "output")

    def test_fractional_trim_window_passes_to_every_content_rendition(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        calls: list[dict[str, object]] = []

        def fake_generate_slate(output_dir: Path) -> Path:
            return _make_stub_slate(output_dir)

        def fake_encode(
            input_path: Path, output_dir: Path, config: object, **kwargs: object
        ) -> Path:
            from civiccast.stream.config import RenditionConfig

            assert isinstance(config, RenditionConfig)
            calls.append(kwargs)
            return _make_stub_rendition_playlist(output_dir, config.name)

        with (
            patch("civiccast.stream.packager.generate_slate", side_effect=fake_generate_slate),
            patch("civiccast.stream.packager._encode_rendition", side_effect=fake_encode),
        ):
            pack_vod_asset(
                input_file,
                tmp_path / "output",
                trim_in_seconds=0.333,
                trim_out_seconds=1.5,
            )

        assert len(calls) == 4
        assert all(c["trim_in_seconds"] == 0.333 for c in calls)
        assert all(c["trim_out_seconds"] == 1.5 for c in calls)


class TestPackVodAssetLadderSelection:
    """``pack_vod_asset`` must not encode renditions taller than the source.

    Regression: Gate A's clerk loop packaged a 640x360 clip through the full
    four-rung ladder. The two upscaled rungs (1080p, 720p) were ~81% of the
    wall time, and the request outran its callers' timeouts. See
    ``tests/stream/test_ladder_selection.py`` for the selection rules; these
    tests pin that the packager actually applies them, including that it
    probes the source when the caller does not supply the dimensions.
    """

    @staticmethod
    def _encoded_configs(
        tmp_path: Path, input_file: Path, **kwargs: object
    ) -> list[RenditionConfig]:
        seen: list[RenditionConfig] = []

        def fake_generate_slate(output_dir: Path) -> Path:
            return _make_stub_slate(output_dir)

        def fake_encode(
            input_path: Path, output_dir: Path, config: object, **_kwargs: object
        ) -> Path:
            assert isinstance(config, RenditionConfig)
            seen.append(config)
            return _make_stub_rendition_playlist(output_dir, config.name)

        with (
            patch("civiccast.stream.packager.generate_slate", side_effect=fake_generate_slate),
            patch("civiccast.stream.packager._encode_rendition", side_effect=fake_encode),
        ):
            pack_vod_asset(input_file, tmp_path / "output", **kwargs)  # type: ignore[arg-type]
        return seen

    def test_supplied_dimensions_drop_the_upscaled_rungs(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        configs = self._encoded_configs(tmp_path, input_file, source_width=640, source_height=360)
        assert [c.name for c in configs] == ["360p", "240p"]
        assert all(c.height <= 360 for c in configs)

    def test_supplied_dimensions_are_not_re_probed(self, tmp_path: Path) -> None:
        """The router hands over ingest's measurement; a second ffprobe on a
        multi-gigabyte meeting recording is pure waste."""
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        with patch("civiccast.stream.packager.probe_video_dimensions") as probe:
            self._encoded_configs(tmp_path, input_file, source_width=640, source_height=360)
        probe.assert_not_called()

    def test_missing_dimensions_are_probed_from_the_input(self, tmp_path: Path) -> None:
        """Callers that never probed (the live finalization worker passes only
        trim) still get the filtered ladder."""
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        with patch(
            "civiccast.stream.packager.probe_video_dimensions", return_value=(640, 360)
        ) as probe:
            configs = self._encoded_configs(tmp_path, input_file)
        probe.assert_called_once_with(input_file)
        assert [c.name for c in configs] == ["360p", "240p"]

    def test_unreadable_probe_falls_back_to_the_full_ladder(self, tmp_path: Path) -> None:
        """A packaging run must never fail or silently shrink because the
        optimisation could not measure its input."""
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        with patch("civiccast.stream.packager.probe_video_dimensions", return_value=None):
            configs = self._encoded_configs(tmp_path, input_file)
        assert [c.name for c in configs] == [c.name for c in ABR_LADDER]

    def test_full_hd_source_still_gets_the_whole_ladder(self, tmp_path: Path) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)
        configs = self._encoded_configs(tmp_path, input_file, source_width=1920, source_height=1080)
        assert [c.name for c in configs] == [c.name for c in ABR_LADDER]

    def test_manifest_lists_exactly_the_selected_renditions_plus_slate(
        self, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "video.mp4"
        input_file.write_bytes(b"\x00" * 32)

        def fake_generate_slate(output_dir: Path) -> Path:
            return _make_stub_slate(output_dir)

        def fake_encode(
            input_path: Path, output_dir: Path, config: object, **_kwargs: object
        ) -> Path:
            assert isinstance(config, RenditionConfig)
            return _make_stub_rendition_playlist(output_dir, config.name)

        with (
            patch("civiccast.stream.packager.generate_slate", side_effect=fake_generate_slate),
            patch("civiccast.stream.packager._encode_rendition", side_effect=fake_encode),
        ):
            result = pack_vod_asset(
                input_file, tmp_path / "output", source_width=640, source_height=360
            )

        assert [r.config.name for r in result.renditions] == ["360p", "240p", "slate"]
        assert result.slate.config.name == "slate"
        manifest_text = result.manifest_path.read_text(encoding="utf-8")
        assert manifest_text.count("#EXT-X-STREAM-INF:") == 3
        assert "360p/playlist.m3u8" in manifest_text
        assert "1080p/playlist.m3u8" not in manifest_text


class TestEncodeRenditionUnit:
    @pytest.mark.parametrize(
        ("filename", "trim_in_seconds", "seek_index", "duration"),
        [
            ("video.mp4", 0.333, 0, "1.167"),
            ("video.ts", 0.333, 2, "1.167"),
            ("video.TS", 0.333, 2, "1.167"),
            ("video.ts", None, None, "1.500"),
            ("video.ts", 0.0, 2, "1.500"),
        ],
    )
    def test_trim_becomes_ffmpeg_seek_and_duration(
        self,
        tmp_path: Path,
        filename: str,
        trim_in_seconds: float | None,
        seek_index: int | None,
        duration: str,
    ) -> None:
        from civiccast.stream._ffmpeg import FfmpegResult
        from civiccast.stream.config import ABR_LADDER

        input_file = tmp_path / filename
        input_file.write_bytes(b"\x00" * 32)
        captured_args: list[str] = []

        def fake_run_ffmpeg(args: list[str], **kwargs: object) -> FfmpegResult:
            captured_args.extend(args)
            playlist = tmp_path / "out" / "240p" / "playlist.m3u8"
            playlist.write_text("#EXTM3U\n#EXT-X-ENDLIST\n", encoding="utf-8")
            return FfmpegResult(returncode=0, stdout="", stderr="")

        with patch("civiccast.stream.packager.run_ffmpeg", side_effect=fake_run_ffmpeg):
            _encode_rendition(
                input_file,
                tmp_path / "out",
                ABR_LADDER[-1],
                trim_in_seconds=trim_in_seconds,
                trim_out_seconds=1.5,
            )

        if seek_index is None:
            assert "-ss" not in captured_args
            assert captured_args[:2] == ["-i", str(input_file)]
        else:
            assert captured_args.count("-ss") == 1
            assert captured_args.index("-ss") == seek_index
            seek = ["-ss", f"{trim_in_seconds:.3f}"]
            source = ["-i", str(input_file)]
            assert captured_args[:4] == (seek + source if seek_index == 0 else source + seek)
        assert "-t" in captured_args
        assert captured_args[captured_args.index("-t") + 1] == duration


# ---------------------------------------------------------------------------
# pack_slate_fallback — unit tests
# ---------------------------------------------------------------------------


class TestPackSlateFallback:
    def test_returns_slate_only_result(self, tmp_path: Path) -> None:
        with patch(
            "civiccast.stream.packager.generate_slate",
            side_effect=lambda d: _make_stub_slate(d),
        ):
            result = pack_slate_fallback(tmp_path / "output")
        assert isinstance(result, SlateOnlyResult)

    def test_manifest_has_single_variant(self, tmp_path: Path) -> None:
        with patch(
            "civiccast.stream.packager.generate_slate",
            side_effect=lambda d: _make_stub_slate(d),
        ):
            result = pack_slate_fallback(tmp_path / "output")
        manifest_text = result.manifest_path.read_text(encoding="utf-8")
        assert manifest_text.count("#EXT-X-STREAM-INF:") == 1

    def test_does_not_regenerate_existing_slate(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        _make_stub_slate(output_dir)  # pre-existing slate

        generate_calls: list[int] = []

        def counting_generate(d: Path) -> Path:
            generate_calls.append(1)
            return _make_stub_slate(d)

        with patch("civiccast.stream.packager.generate_slate", side_effect=counting_generate):
            pack_slate_fallback(output_dir)

        assert generate_calls == []  # slate already existed — no regeneration

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "new_output"
        assert not output_dir.exists()

        with patch(
            "civiccast.stream.packager.generate_slate",
            side_effect=lambda d: _make_stub_slate(d),
        ):
            pack_slate_fallback(output_dir)

        assert output_dir.exists()


def test_packager_records_actual_fractional_source_range(tmp_path: Path) -> None:
    import json

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    with (
        patch("civiccast.stream.packager.generate_slate", side_effect=_make_stub_slate),
        patch(
            "civiccast.stream.packager._encode_rendition",
            side_effect=lambda source, output, config, **kwargs: _make_stub_rendition_playlist(
                output, config.name
            ),
        ),
    ):
        result = pack_vod_asset(
            source, tmp_path / "package", trim_in_seconds=2.25, trim_out_seconds=65.5
        )
    assert json.loads((result.output_dir / "playback-timeline.json").read_text()) == {
        "version": 1,
        "source_start_seconds": 2.25,
        "source_end_seconds": 65.5,
    }
