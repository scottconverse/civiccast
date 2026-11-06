# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""FT-8: FFmpeg time arguments are plain decimals, never scientific notation.

Field incident (r7, 2026-11-06 09:02): a 90 s repeating programme's rollover
prepared a window whose start offset had drifted to 5.6e-05 s. ``f"{x:g}"``
printed it as ``5.6e-05``; FFmpeg rejected ``-ss 5.6e-05`` ("Invalid duration
for option ss"), preparation failed and the channel aired the slate for about
28 minutes. ``:g`` also keeps only six significant digits, so a long offset
such as 74564.970002 s was silently rounded to ``74565``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from civiccast.egress import loudness_ride as lr
from civiccast.egress.models import CanonicalProfile, EgressSourcePlan, EgressSourceSegment
from civiccast.egress.preparer import build_conform_source_args, build_loudnorm_probe_args
from civiccast.egress.runtime import write_concat_plan
from civiccast.stream import loudness as loudness_module
from civiccast.stream._ffmpeg import ffmpeg_seconds

REPO = Path(__file__).resolve().parents[2]
_SCIENTIFIC = re.compile(r"\d[eE][+-]?\d")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5.6e-05, "0.000056"),
        (0.0, "0"),
        (-0.0, "0"),
        (90, "90"),
        (12.5, "12.5"),
        (89.9999, "89.9999"),
        (1_000_000.0, "1000000"),
        (74564.970002, "74564.970002"),
        (1e-9, "0"),
    ],
)
def test_ffmpeg_seconds_prints_plain_decimals(value, expected):
    assert ffmpeg_seconds(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_ffmpeg_seconds_rejects_non_finite(value):
    with pytest.raises(ValueError):
        ffmpeg_seconds(value)


def _segment(tmp_path: Path) -> EgressSourceSegment:
    return EgressSourceSegment(
        label="Phase 4 public 90 s test",
        path=str(tmp_path / "rc4-public-90s.mp4"),
        duration_seconds=89.9999,
        inpoint_seconds=5.6e-05,
    )


def _value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def _assert_plain(args: list[str]) -> None:
    assert _value_after(args, "-ss") == "0.000056"
    assert _value_after(args, "-t") == "89.9999"
    assert not any(_SCIENTIFIC.fullmatch(a) or re.fullmatch(r"[\d.]+e-\d+", a) for a in args)


def test_conform_args_use_plain_decimal_offset(tmp_path):
    args = build_conform_source_args(
        source_path=tmp_path / "rc4-public-90s.mp4",
        output_path=tmp_path / "out.ts",
        segment=_segment(tmp_path),
        profile=CanonicalProfile(width=640, height=360, video_bitrate_kbps=1200),
    )
    _assert_plain(args)


def test_loudnorm_probe_args_use_plain_decimal_offset(tmp_path):
    args = build_loudnorm_probe_args(
        source_path=tmp_path / "rc4-public-90s.mp4",
        segment=_segment(tmp_path),
        loudness_target_lufs=-24.0,
    )
    _assert_plain(args)


def test_loudness_ride_args_use_plain_decimal_offset(tmp_path):
    params = lr.RideParams(target_lufs=-16.0)
    seg = _segment(tmp_path)
    src = tmp_path / "rc4-public-90s.mp4"
    _assert_plain(lr.build_source_series_args(source_path=src, segment=seg, params=params))
    _assert_plain(lr.build_decoder_args(source_path=src, segment=seg, params=params))
    _assert_plain(
        lr.build_video_from_source_args(
            source_path=src,
            audio_path=tmp_path / "a.wav",
            output_path=tmp_path / "out.ts",
            segment=seg,
            profile=CanonicalProfile(width=640, height=360, video_bitrate_kbps=1200),
        )
    )


def test_concat_plan_uses_plain_decimal_inpoint(tmp_path):
    seg = EgressSourceSegment(
        label="Phase 4 public 90 s test",
        path=str(tmp_path / "prepared.ts"),
        duration_seconds=89.9999,
        inpoint_seconds=5.6e-05,
        outpoint_seconds=90.000046,
    )
    plan_path = tmp_path / "plan.ffconcat"
    write_concat_plan(plan_path, EgressSourcePlan(channel_id="public", segments=[seg]))
    text = plan_path.read_text(encoding="utf-8")
    assert "inpoint 0.000056" in text
    assert "outpoint 90.000046" in text
    assert not _SCIENTIFIC.search(text.replace(str(tmp_path), ""))


def test_loudness_gate_probe_uses_plain_decimals(tmp_path, monkeypatch):
    media = tmp_path / "meeting.wav"
    media.write_bytes(b"RIFFplaceholder")
    seen: list[list[str]] = []

    def fake_run(args):
        seen.append(list(args))
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": "", "stderr": "Integrated loudness:\n    I:  -16.2 LUFS\n"},
        )()

    monkeypatch.setattr(loudness_module, "check_ffmpeg", lambda: ("7.0", True))
    monkeypatch.setattr(loudness_module, "run_ffmpeg", fake_run)
    loudness_module.check_loudness(
        media_path=media,
        target_lufs=-16.0,
        tolerance_lufs=1.0,
        probe_start_seconds=5.6e-05,
        probe_duration_seconds=1_000_000.0,
    )
    assert seen, "the gate never invoked ffmpeg"
    assert _value_after(seen[0], "-ss") == "0.000056"
    assert _value_after(seen[0], "-t") == "1000000"


def test_no_g_format_reaches_an_ffmpeg_time_option():
    """Source guard: every time option FFmpeg parses goes through ffmpeg_seconds."""
    pattern = re.compile(r'(\["-(ss|t|to|sseof)",\s*f"[^"]*:g\}|"(inpoint|outpoint) \{[^}]*:g\}")')
    offenders = []
    for path in (REPO / "civiccast").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(REPO)}:{line}")
        # multi-line form: "-t",\n  f"{...:g}"
        for match in re.finditer(r'"-(ss|t|to)",\s*\n\s*f"\{[^}]*:g\}"', text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert offenders == []
