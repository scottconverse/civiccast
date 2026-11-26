# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Unit tests for the pure native-Windows encoder-selection decision (no gi, no platform read)."""

from __future__ import annotations

import pytest

from civiccast.egress.errors import EncoderUnavailableError
from civiccast.egress.gst.encoder_probe import EncoderDecision, decide_encoder


def _present(*names: str):
    """A probe that reports the given factories as available hardware."""
    seen: list[str] = []

    def probe(name: str) -> bool:
        seen.append(name)
        return name in names

    probe.seen = seen  # type: ignore[attr-defined]
    return probe


def test_non_windows_never_gates() -> None:
    # WSL/Linux path is byte-unchanged: no override, and the probe is never consulted.
    probe = _present()
    decision = decide_encoder(
        codec="h264_vaapi", is_windows=False, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override=None, warning=None)
    assert probe.seen == []  # type: ignore[attr-defined]


def test_windows_hardware_present_proceeds() -> None:
    probe = _present("mfh264enc")
    decision = decide_encoder(
        codec="h264_vaapi", is_windows=True, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override="mfh264enc", warning=None)


def test_windows_software_codec_never_probed() -> None:
    # libx264 resolves to openh264enc (already software) -> no hardware gate, probe untouched.
    probe = _present()
    decision = decide_encoder(
        codec="libx264", is_windows=True, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override="openh264enc", warning=None)
    assert probe.seen == []  # type: ignore[attr-defined]


def test_windows_nvenc_present_proceeds() -> None:
    probe = _present("nvh264enc")
    decision = decide_encoder(
        codec="h264_nvenc", is_windows=True, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override="nvh264enc", warning=None)


def test_windows_h264_absent_no_fallback_refuses() -> None:
    probe = _present()  # nothing available
    with pytest.raises(EncoderUnavailableError) as exc:
        decide_encoder(
            codec="h264_vaapi", is_windows=True, allow_software_fallback=False, probe=probe
        )
    msg = str(exc.value)
    assert "software" in msg.lower()
    assert "settings" in msg.lower()  # points the operator at the channel setting


def test_windows_h264_absent_with_fallback_substitutes_software() -> None:
    probe = _present()
    decision = decide_encoder(
        codec="h264_vaapi", is_windows=True, allow_software_fallback=True, probe=probe
    )
    assert decision.encoder_override == "openh264enc"
    assert decision.warning is not None
    assert "cpu" in decision.warning.lower()


def test_windows_hevc_absent_refuses_even_with_fallback() -> None:
    # HEVC is hardware-only; fallback does NOT rescue it.
    probe = _present()
    with pytest.raises(EncoderUnavailableError) as exc:
        decide_encoder(
            codec="hevc_vaapi", is_windows=True, allow_software_fallback=True, probe=probe
        )
    assert "hevc" in str(exc.value).lower()


def test_windows_hevc_absent_no_fallback_refuses() -> None:
    probe = _present()
    with pytest.raises(EncoderUnavailableError):
        decide_encoder(
            codec="hevc_vaapi", is_windows=True, allow_software_fallback=False, probe=probe
        )


def test_windows_software_x265_not_refused_as_hardware_hevc() -> None:
    # MAJOR regression: libx265/hevc/h265 map to the SOFTWARE x265enc (contains "265"
    # but has no MF/NV12 dependency). It must be treated as software and used unchanged,
    # NOT swept into the hardware-HEVC refusal, and the probe must not be consulted.
    probe = _present()
    decision = decide_encoder(
        codec="libx265", is_windows=True, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override="x265enc", warning=None)
    assert probe.seen == []  # type: ignore[attr-defined]


def test_windows_hevc_present_proceeds() -> None:
    # HEVC now works on native Windows via the NV12 input pinned in
    # bridge._apply_encoder_fixups, so a present hardware HEVC encoder is used, not refused.
    probe = _present("mfh265enc")
    decision = decide_encoder(
        codec="hevc_vaapi", is_windows=True, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override="mfh265enc", warning=None)


def test_windows_h264_works_when_only_hevc_absent() -> None:
    # HEVC-less machine (owner beta requirement): H.264 hardware present, HEVC hardware
    # absent. An H.264 channel must start normally -- H.264 never depends on HEVC.
    probe = _present("mfh264enc")  # mfh265enc deliberately NOT present
    decision = decide_encoder(
        codec="h264_vaapi", is_windows=True, allow_software_fallback=False, probe=probe
    )
    assert decision == EncoderDecision(encoder_override="mfh264enc", warning=None)


@pytest.mark.parametrize(
    "available,expected",
    [
        # Media Foundation first: on an NVIDIA box mfh264enc is the Media Foundation
        # NVIDIA H.264 Encoder MFT, so the GPU is still used. nvh264enc caused CTRL
        # stall worker restarts in the field; it is now the second choice.
        (("nvh264enc", "mfh264enc"), "mfh264enc"),
        (("nvh264enc",), "nvh264enc"),
        (("mfh264enc",), "mfh264enc"),
    ],
)
def test_declarative_h264_automatically_prefers_available_hardware(available, expected):
    decision = decide_encoder(
        codec="h264", is_windows=True, allow_software_fallback=True, probe=_present(*available)
    )
    assert decision.encoder_override == expected


def test_declarative_h264_preserves_existing_cpu_default_without_hardware():
    decision = decide_encoder(
        codec="h264", is_windows=True, allow_software_fallback=False, probe=_present()
    )
    assert decision.encoder_override == "openh264enc"
    assert decision.warning is not None
    assert "CPU" in decision.warning
