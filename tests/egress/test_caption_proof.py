# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S11a caption decode-back proof + the fail-closed caption_status gate."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from civiccast.captions.models import CaptionCue
from civiccast.egress.caption_proof import (
    GST_CC_ELEMENTS,
    build_caption_status_provider,
    caption_lane_report,
    decode_embedded_captions,
    sample_caption_decode_back,
)
from civiccast.egress.models import EgressCaptionProofSample
from civiccast.egress.store import InMemoryEgressStore

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_REAL_CAPTION_FIXTURE = _FIXTURES_DIR / "cea708_test_caption.mpegts"
_NO_CAPTION_FIXTURE = _FIXTURES_DIR / "cea708_no_captions.mpegts"
_FIXTURE_CAPTION_TEXT = "CIVICCAST CEA708 TEST."

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="real-fixture decode-back needs ffmpeg on PATH"
)


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


def _cue(n: int, start: float, end: float, text: str) -> CaptionCue:
    return CaptionCue(
        cue_id=f"c{n}",
        start_seconds=start,
        end_seconds=end,
        text=text,
        confidence=1.0,
        low_confidence=False,
    )


_SRT = """1
00:00:00,200 --> 00:00:01,500
HELLO CIVICCAST

2
00:00:01,800 --> 00:00:02,800
CAPTION TEST TWO
"""


def test_decode_embedded_captions_parses_subcc_srt() -> None:
    cues = decode_embedded_captions(Path("out.ts"), runner=lambda _a: _result(stdout=_SRT))
    assert [c.text for c in cues] == ["HELLO CIVICCAST", "CAPTION TEST TWO"]


def test_decode_embedded_captions_escapes_windows_drive_colon_through_both_parsers() -> None:
    # The lavfi ``movie=`` filename crosses two escape-consuming ffmpeg parsers, so a
    # drive colon must be double-escaped (``C\\:``). Single-level ``\:`` made ffmpeg
    # split ``C:/...`` at the colon and open the file ``C`` — captions were embedded
    # but the proof always read not-verified on native Windows.
    seen: list[list[str]] = []

    def runner(args: list[str]) -> Any:
        seen.append(args)
        return _result(stdout=_SRT)

    decode_embedded_captions(Path("C:/tmp/out.ts"), runner=runner)
    movie_arg = seen[0][seen[0].index("-i") + 1]
    assert movie_arg == (
        "movie=C\\\\:/tmp/out.ts"
        ":format_opts='probesize=20000000\\:analyzeduration=20000000"
        "\\:scan_all_pmts=1'[out0+subcc]"
    )


def test_decode_embedded_captions_empty_when_no_captions_or_error() -> None:
    assert decode_embedded_captions(Path("out.ts"), runner=lambda _a: _result(stdout="")) == []


def test_decode_embedded_captions_times_out_fail_closed() -> None:
    seen: dict[str, object] = {}

    def timed_out_runner(args: list[str], *, timeout: float) -> Any:
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(args, timeout)

    assert decode_embedded_captions(Path("out.ts"), runner=timed_out_runner) == []
    assert seen["timeout"] == 15.0
    assert (
        decode_embedded_captions(
            Path("out.ts"), runner=lambda _a: _result(returncode=1, stdout=_SRT)
        )
        == []
    )


def test_sample_decode_back_pass_when_captions_match() -> None:
    expected = [_cue(1, 0.2, 1.5, "HELLO CIVICCAST"), _cue(2, 1.8, 2.8, "CAPTION TEST TWO")]
    sample = sample_caption_decode_back(
        channel_id="gov",
        emitted_stream_path=Path("out.ts"),
        expected_cues=expected,
        mode="cea-708",
        runner=lambda _a: _result(stdout=_SRT),
        clock=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert sample.status == "PASS"
    assert sample.caption_status == "on"
    assert sample.matched_cue_count == 2
    assert sample.mode == "cea-708"


def test_sample_decode_back_fail_when_no_captions_survive() -> None:
    expected = [_cue(1, 0.2, 1.5, "HELLO CIVICCAST")]
    sample = sample_caption_decode_back(
        channel_id="gov",
        emitted_stream_path=Path("out.ts"),
        expected_cues=expected,
        mode="cea-708",
        runner=lambda _a: _result(stdout=""),
        clock=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert sample.status == "FAIL"
    assert sample.caption_status == "not-verified"
    assert sample.blocker == "EGRESS_CAPTION_DECODE_BACK_MISMATCH"


def _append(
    store: InMemoryEgressStore, *, sampled_at: datetime, status: str, caption_status: str
) -> None:
    store.append_caption_proof_sample(
        EgressCaptionProofSample(
            channel_id="gov",
            sampled_at=sampled_at,
            status=status,  # type: ignore[arg-type]
            caption_status=caption_status,  # type: ignore[arg-type]
            mode="cea-708",
            decoder_name="ffmpeg-subcc",
            expected_cue_count=1,
            decoded_cue_count=1 if status == "PASS" else 0,
            matched_cue_count=1 if status == "PASS" else 0,
            proof_boundary="egress-caption-embed-to-emitted-stream-decode-back",
        )
    )


def test_caption_status_provider_on_only_for_fresh_pass() -> None:
    store = InMemoryEgressStore()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    provider = build_caption_status_provider(store, freshness_seconds=120, clock=lambda: now)
    assert provider("gov") == "not-verified"  # no sample
    _append(store, sampled_at=now - timedelta(seconds=10), status="PASS", caption_status="on")
    assert provider("gov") == "on"


def test_caption_status_provider_not_verified_when_stale() -> None:
    store = InMemoryEgressStore()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _append(store, sampled_at=now - timedelta(seconds=600), status="PASS", caption_status="on")
    provider = build_caption_status_provider(store, freshness_seconds=120, clock=lambda: now)
    assert provider("gov") == "not-verified"


def test_caption_status_provider_not_verified_for_fail() -> None:
    store = InMemoryEgressStore()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _append(store, sampled_at=now, status="FAIL", caption_status="not-verified")
    provider = build_caption_status_provider(store, freshness_seconds=120, clock=lambda: now)
    assert provider("gov") == "not-verified"


@requires_ffmpeg
def test_decode_embedded_captions_against_a_real_cea708_fixture() -> None:
    """Decoder-output-parser test against a REAL recorded fixture (not a mocked
    runner): ``tests/egress/fixtures/cea708_test_caption.mpegts`` is a genuine,
    tiny (~18 KB) MPEG-TS file with real ATSC A/53 CEA-608-in-708 SEI data hand-built
    and verified against this exact production code path (see
    ``civiccast/installer/cea708_verification.py``'s module docstring for how it was
    built). This exercises the true ``ffmpeg`` binary via the default ``run_ffmpeg``
    runner -- no mock -- proving the parser against real decoder output, including
    the ASS ``{\\an7}`` position-tag stripping ``_clean_caption_text`` needs (a real
    gap this fixture caught: ffmpeg's eia_608/cc_dec -> srt output always wraps
    decoded text in an ASS override block that a hand-written SRT fixture never
    would)."""
    cues = decode_embedded_captions(_REAL_CAPTION_FIXTURE, source_id="fixture-test")
    assert [c.text for c in cues] == [_FIXTURE_CAPTION_TEXT]
    assert cues[0].start_seconds == pytest.approx(0.0, abs=0.05)
    assert cues[0].end_seconds == pytest.approx(1.6, abs=0.2)


@requires_ffmpeg
def test_real_caption_capture_preserves_long_running_clock(tmp_path):
    from civiccast.egress.caption_proof_worker import capture_emitted_segment
    from civiccast.egress.models import EgressConfig, EgressSinkSpec

    shifted = tmp_path / "long-clock.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-copyts",
            "-itsoffset",
            "133200",
            "-i",
            str(_REAL_CAPTION_FIXTURE),
            "-c",
            "copy",
            "-mpegts_copyts",
            "1",
            str(shifted),
        ],
        check=True,
        timeout=15,
        capture_output=True,
    )
    store = InMemoryEgressStore()
    store.upsert_config(
        EgressConfig(
            channel_id="gov",
            enabled=True,
            slate_message="capture",
            sinks=[EgressSinkSpec(kind="file", label="capture", uri=str(shifted))],
        )
    )
    segment = capture_emitted_segment(store, "gov", work_dir=tmp_path, capture_seconds=6)
    assert segment is not None
    expected = _cue(1, 133201.4, 133203, _FIXTURE_CAPTION_TEXT)
    sample = sample_caption_decode_back(
        channel_id="gov",
        emitted_stream_path=segment,
        expected_cues=[expected],
        mode="cea-708",
        align_transport_clock=True,
    )
    assert sample.status == "PASS", sample
    wrong = sample_caption_decode_back(
        channel_id="gov",
        emitted_stream_path=segment,
        expected_cues=[expected.model_copy(update={"text": "WRONG CAPTION"})],
        mode="cea-708",
        align_transport_clock=True,
    )
    assert wrong.status == "FAIL"


@requires_ffmpeg
def test_decode_embedded_captions_empty_for_real_stream_without_captions() -> None:
    """Same real video, no embedded captions -- the parser must report zero cues,
    not fabricate one, when nothing was actually embedded."""
    assert decode_embedded_captions(_NO_CAPTION_FIXTURE, source_id="fixture-test") == []


@requires_ffmpeg
def test_sample_decode_back_passes_against_the_real_fixture() -> None:
    """Full real round trip: real ffmpeg decode + real comparison, PASS."""
    expected = [_cue(1, 0.0, 1.6, _FIXTURE_CAPTION_TEXT)]
    sample = sample_caption_decode_back(
        channel_id="fixture-channel",
        emitted_stream_path=_REAL_CAPTION_FIXTURE,
        expected_cues=expected,
        mode="cea-708",
        clock=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert sample.status == "PASS"
    assert sample.caption_status == "on"
    assert sample.matched_cue_count == 1
    assert sample.blocker is None


@requires_ffmpeg
def test_sample_decode_back_fails_against_the_real_no_caption_fixture() -> None:
    """Full real round trip against the stream with no embedded captions: FAIL,
    fail-closed, never a fabricated PASS."""
    expected = [_cue(1, 0.0, 1.6, _FIXTURE_CAPTION_TEXT)]
    sample = sample_caption_decode_back(
        channel_id="fixture-channel",
        emitted_stream_path=_NO_CAPTION_FIXTURE,
        expected_cues=expected,
        mode="cea-708",
        clock=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert sample.status == "FAIL"
    assert sample.caption_status == "not-verified"
    assert sample.decoded_cue_count == 0


def test_caption_lane_report_capability_summary() -> None:
    full = caption_lane_report(
        ffmpeg_filters="... readeia608 V->V ... ebur128 ...",
        gst_elements=set(GST_CC_ELEMENTS),
    )
    assert full.decode_back_capable is True
    assert full.gst_embed_available is True
    assert full.ffmpeg_embed == "sidecar-only"

    none = caption_lane_report(ffmpeg_filters="loudnorm ebur128", gst_elements=set())
    assert none.decode_back_capable is False
    assert none.gst_embed_available is False

    # decode-back ready but the gst embed lane is only partially installed.
    partial = caption_lane_report(ffmpeg_filters="readeia608", gst_elements={"cccombiner"})
    assert partial.decode_back_capable is True
    assert partial.gst_embed_available is False
