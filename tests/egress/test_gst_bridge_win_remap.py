# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Unit tests for the Windows-only VAAPI -> Media Foundation encoder remap."""

from __future__ import annotations

import pytest

from civiccast.egress.gst.bridge import encode_chain_from_profile, gst_encoder_name
from civiccast.egress.models import CanonicalProfile


def test_h264_vaapi_remaps_to_mf_on_windows() -> None:
    assert gst_encoder_name("h264_vaapi", is_windows=True) == "mfh264enc"


def test_hevc_vaapi_remaps_to_mf_on_windows() -> None:
    assert gst_encoder_name("hevc_vaapi", is_windows=True) == "mfh265enc"


def test_h264_vaapi_stays_va_on_non_windows() -> None:
    assert gst_encoder_name("h264_vaapi", is_windows=False) == "vah264enc"


def test_hevc_vaapi_stays_va_on_non_windows() -> None:
    assert gst_encoder_name("hevc_vaapi", is_windows=False) == "vah265enc"


def test_nvenc_unchanged_on_windows() -> None:
    assert gst_encoder_name("h264_nvenc", is_windows=True) == "nvh264enc"


def test_software_encoder_unchanged_on_windows() -> None:
    assert gst_encoder_name("libx264", is_windows=True) == "openh264enc"


def test_vaapi_remap_is_case_insensitive() -> None:
    assert gst_encoder_name("H264_VAAPI", is_windows=True) == "mfh264enc"


def test_encoder_override_forces_factory() -> None:
    # The software-fallback path passes encoder_override to force openh264enc regardless
    # of the profile's configured codec (which would otherwise map to a hardware encoder).
    profile = CanonicalProfile(video_codec="h264_vaapi")
    specs = encode_chain_from_profile(profile, encoder_override="openh264enc")
    assert "openh264enc" in [s.factory for s in specs]


def _video_capsfilter(specs):
    return next(
        s
        for s in specs
        if s.factory == "capsfilter" and str(s.props.get("caps", "")).startswith("video/x-raw")
    )


def test_openh264_bitrate_converted_to_bits_per_second() -> None:
    # openh264enc's bitrate property is in bits/sec, unlike mf/nv/x264 (kbit/sec). The
    # profile carries kbit/sec, so the software path must convert x1000 or it under-
    # delivers ~2x. Fix lives in bridge.py (graph.py stays governance-clean).
    profile = CanonicalProfile(video_codec="libx264", video_bitrate_kbps=8000)  # -> openh264enc
    enc = next(s for s in encode_chain_from_profile(profile) if s.factory == "openh264enc")
    assert enc.props["bitrate"] == 8_000_000  # 8000 kbit/s expressed as bit/s


def test_mf_h264_pins_nv12_input_format() -> None:
    # Media Foundation encoders require an explicit NV12 input; pin it on the conform
    # capsfilter (MF-only) so the pipeline negotiates.
    specs = encode_chain_from_profile(
        CanonicalProfile(video_codec="h264_vaapi"), encoder_override="mfh264enc"
    )
    assert "format=NV12" in _video_capsfilter(specs).props["caps"]


def test_mf_h265_pins_nv12_input_format() -> None:
    specs = encode_chain_from_profile(
        CanonicalProfile(video_codec="hevc_vaapi"), encoder_override="mfh265enc"
    )
    assert "format=NV12" in _video_capsfilter(specs).props["caps"]


def test_non_mf_encoder_does_not_pin_format() -> None:
    # NVENC / software negotiate their own input; do not constrain their format.
    specs = encode_chain_from_profile(
        CanonicalProfile(video_codec="h264_nvenc"), encoder_override="nvh264enc"
    )
    assert "format=" not in _video_capsfilter(specs).props["caps"]


# --- keyframe cadence (RC4 host run 2026-11-04) -------------------------------
# The live-HLS sink stream-copies video (captions survive), so segment length IS
# the encoder's keyframe interval. Only openh264/x264 were pinned; nvh264enc's
# own default (75 frames) gave 2.5 s segments under a "2 s" playlist target, and
# mfh264enc's default (-1, "automatic") can mean one keyframe per stream.


def _encoder_spec(specs):
    return next(s for s in specs if s.factory.endswith(("h264enc", "h265enc")))


@pytest.mark.parametrize(
    ("codec", "encoder"),
    [
        ("h264_nvenc", "nvh264enc"),
        ("hevc_nvenc", "nvh265enc"),
        ("h264_vaapi", "mfh264enc"),
        ("hevc_vaapi", "mfh265enc"),
        ("h264_nvenc", "d3d12h264enc"),
    ],
)
def test_hardware_encoders_get_the_profile_keyframe_interval(codec, encoder) -> None:
    profile = CanonicalProfile(video_codec=codec, gop_size=60)
    specs = encode_chain_from_profile(profile, encoder_override=encoder)
    assert _encoder_spec(specs).props["gop-size"] == 60


def test_openh264_keeps_its_existing_keyframe_interval() -> None:
    specs = encode_chain_from_profile(
        CanonicalProfile(video_codec="libopenh264", gop_size=45), encoder_override="openh264enc"
    )
    assert _encoder_spec(specs).props["gop-size"] == 45
