# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Issue #348: the operator's station logo -- storage, checks, playout, board.

Covers the pieces the operator screens and the playout graph rely on:

* upload checks (a real PNG/JPEG is accepted; SVG, oversized, undecodable, and
  over-size files are refused with a plain message);
* storing / removing the one fixed file the service owns;
* ``graphics_overlay_leg_from_config`` drawing the logo only when it is switched
  on AND present, and falling back to no logo (never raising) otherwise;
* a CG board zone whose ``image_asset_ref`` is ``station-logo`` resolving to the
  same file, and resolving to nothing when no logo is uploaded.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from civiccast.egress.gst.bridge import graphics_overlay_leg_from_config
from civiccast.egress.gst.graphics_overlay import write_rgba_png
from civiccast.egress.models import (
    CanonicalProfile,
    EgressConfig,
    EgressSinkSpec,
)
from civiccast.egress.station_logo import (
    MAX_LOGO_BYTES,
    STATION_LOGO_REF,
    StationLogoError,
    board_uses_station_logo,
    logo_file_looks_like_an_image,
    remove_station_logo,
    resolve_station_logo,
    station_logo_bytes,
    station_logo_path,
    station_logo_present,
    station_logo_version,
    validate_logo_bytes,
    with_station_logo,
    write_station_logo,
)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_needs_ffmpeg = pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg is not installed")
_needs_ffprobe = pytest.mark.skipif(_FFPROBE is None, reason="ffprobe is not installed")


def _png_bytes(tmp_path: Path, width: int = 16, height: int = 16) -> bytes:
    path = tmp_path / f"logo-{width}x{height}.png"
    write_rgba_png(path, width, height, bytes([200, 30, 30, 255]) * (width * height))
    return path.read_bytes()


def _jpeg_bytes(tmp_path: Path) -> bytes:
    png = tmp_path / "source.png"
    jpg = tmp_path / "source.jpg"
    write_rgba_png(png, 32, 24, bytes([20, 90, 200, 255]) * (32 * 24))
    subprocess.run(
        [_FFMPEG, "-y", "-v", "error", "-i", str(png), str(jpg)],  # type: ignore[list-item]
        check=True,
    )
    return jpg.read_bytes()


def _config(**overrides: object) -> EgressConfig:
    defaults: dict[str, object] = {
        "channel_id": "ch1",
        "enabled": True,
        "slate_message": "Please stand by",
        "sinks": [EgressSinkSpec(kind="udp-ts", label="head", uri="udp://10.0.0.9:5000")],
        "canonical_profile": CanonicalProfile(width=1280, height=720),
    }
    defaults.update(overrides)
    return EgressConfig(**defaults)  # type: ignore[arg-type]


# --- upload checks -----------------------------------------------------------


@_needs_ffprobe
def test_a_real_png_is_accepted(tmp_path: Path) -> None:
    assert validate_logo_bytes(_png_bytes(tmp_path, 40, 20)) == ("png", 40, 20)


@_needs_ffprobe
@_needs_ffmpeg
def test_a_real_jpeg_is_accepted(tmp_path: Path) -> None:
    assert validate_logo_bytes(_jpeg_bytes(tmp_path)) == ("jpeg", 32, 24)


def test_svg_is_refused_with_a_plain_message() -> None:
    with pytest.raises(StationLogoError) as excinfo:
        validate_logo_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    assert "PNG or JPEG" in str(excinfo.value)


def test_a_gif_is_refused() -> None:
    with pytest.raises(StationLogoError):
        validate_logo_bytes(b"GIF89a" + b"\x00" * 40)


def test_bytes_over_two_megabytes_are_refused() -> None:
    with pytest.raises(StationLogoError) as excinfo:
        validate_logo_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_LOGO_BYTES)
    assert "2 MB" in str(excinfo.value)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(StationLogoError):
        validate_logo_bytes(b"")


@_needs_ffprobe
def test_a_png_header_over_garbage_is_refused() -> None:
    with pytest.raises(StationLogoError):
        validate_logo_bytes(b"\x89PNG\r\n\x1a\n" + b"not really a png" * 20)


@_needs_ffprobe
def test_an_image_bigger_than_1920x1080_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StationLogoError) as excinfo:
        validate_logo_bytes(_png_bytes(tmp_path, 1921, 8))
    assert "1920 x 1080" in str(excinfo.value)


# --- the file the service owns ------------------------------------------------


@_needs_ffprobe
def test_write_read_and_remove_round_trip(tmp_path: Path) -> None:
    assert station_logo_present(tmp_path, "ch1") is False
    write_station_logo(tmp_path, "ch1", _png_bytes(tmp_path))
    assert station_logo_present(tmp_path, "ch1") is True
    assert station_logo_path(tmp_path, "ch1") == tmp_path / "ch1" / "branding" / "logo.png"
    assert station_logo_bytes(tmp_path, "ch1") == station_logo_path(tmp_path, "ch1").stat().st_size
    assert remove_station_logo(tmp_path, "ch1") is True
    assert station_logo_present(tmp_path, "ch1") is False
    # Removing a logo that is not there is not an error.
    assert remove_station_logo(tmp_path, "ch1") is False


def test_a_stored_file_whose_header_is_not_an_image_is_reported_as_bad(tmp_path: Path) -> None:
    path = station_logo_path(tmp_path, "ch1")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not an image")
    assert logo_file_looks_like_an_image(path) is False


# --- the program overlay (station bug) ---------------------------------------


@_needs_ffprobe
def test_the_logo_is_drawn_when_switched_on_and_present(tmp_path: Path) -> None:
    write_station_logo(tmp_path, "ch1", _png_bytes(tmp_path))
    config = _config(graphics_overlay_enabled=True, graphics_overlay_logo_enabled=True)
    leg = graphics_overlay_leg_from_config(config, render_dir=tmp_path / "ch1")
    assert leg is not None
    names = [layer.name for layer in leg.layers]
    assert names == ["station_bug"]
    assert Path(leg.layers[0].image_path) == tmp_path / "ch1" / "branding" / "logo.png"


@_needs_ffprobe
def test_logo_and_lower_third_are_drawn_together(tmp_path: Path) -> None:
    write_station_logo(tmp_path, "ch1", _png_bytes(tmp_path))
    config = _config(
        graphics_overlay_enabled=True,
        graphics_overlay_logo_enabled=True,
        graphics_overlay_lower_third_text="Town Council - Live",
    )
    leg = graphics_overlay_leg_from_config(config, render_dir=tmp_path / "ch1")
    assert leg is not None
    assert {layer.name for layer in leg.layers} == {"station_bug", "lower_third"}
    assert all(Path(layer.image_path).exists() for layer in leg.layers)


def test_the_logo_is_not_drawn_when_the_switch_is_off(tmp_path: Path) -> None:
    config = _config(graphics_overlay_enabled=True, graphics_overlay_logo_enabled=False)
    assert graphics_overlay_leg_from_config(config, render_dir=tmp_path / "ch1") is None


def test_a_missing_logo_falls_back_to_no_logo_instead_of_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(graphics_overlay_enabled=True, graphics_overlay_logo_enabled=True)
    leg = graphics_overlay_leg_from_config(config, render_dir=tmp_path / "ch1")
    assert leg is None
    assert "airing without the logo" in caplog.text


def test_a_missing_logo_still_airs_the_lower_third(tmp_path: Path) -> None:
    config = _config(
        graphics_overlay_enabled=True,
        graphics_overlay_logo_enabled=True,
        graphics_overlay_lower_third_text="Town Council - Live",
    )
    leg = graphics_overlay_leg_from_config(config, render_dir=tmp_path / "ch1")
    assert leg is not None
    assert [layer.name for layer in leg.layers] == ["lower_third"]


# --- the CG board logo zone --------------------------------------------------


class _Zone:
    def __init__(self, content: dict[str, object]) -> None:
        self.content = content


class _Snapshot:
    def __init__(self, zones: list[_Zone]) -> None:
        self.zones = zones


class _Board:
    def __init__(self, zones: list[_Zone]) -> None:
        self.snapshot = _Snapshot(zones)


def test_a_board_logo_zone_resolves_to_the_uploaded_logo(tmp_path: Path) -> None:
    path = station_logo_path(tmp_path, "ch1")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
    board = _Board([_Zone({"image_asset_ref": STATION_LOGO_REF})])
    assert board_uses_station_logo(board) is True  # type: ignore[arg-type]
    resolver = with_station_logo(None, work_dir=tmp_path, channel_id="ch1")
    assert resolver(STATION_LOGO_REF) == path
    # Other refs still go to the wrapped resolver.
    assert resolver("some-asset") is None


def test_a_board_logo_zone_with_no_upload_resolves_to_nothing(tmp_path: Path) -> None:
    resolver = with_station_logo(None, work_dir=tmp_path, channel_id="ch1")
    assert resolver(STATION_LOGO_REF) is None


def test_the_board_cache_version_follows_the_logo_file(tmp_path: Path) -> None:
    board = _Board([_Zone({"image_asset_ref": STATION_LOGO_REF})])
    assert station_logo_version(board, tmp_path, "ch1") == "station-logo-absent"  # type: ignore[arg-type]
    path = station_logo_path(tmp_path, "ch1")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
    first = station_logo_version(board, tmp_path, "ch1")  # type: ignore[arg-type]
    assert first is not None and first.startswith("station-logo-")
    # A board that does not use the logo keeps the cache key it had before.
    assert station_logo_version(_Board([]), tmp_path, "ch1") is None  # type: ignore[arg-type]


def test_resolve_station_logo_returns_the_file_only_when_present(tmp_path: Path) -> None:
    assert resolve_station_logo(tmp_path, "ch1") is None
    path = station_logo_path(tmp_path, "ch1")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
    assert resolve_station_logo(tmp_path, "ch1") == path
