# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S11a caption decode-back proof loop (run_once logic + live-capture selection)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from civiccast.captions.models import CaptionCue
from civiccast.egress.caption_proof import (
    DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS,
    DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS,
)
from civiccast.egress.caption_proof_worker import (
    CaptionProofWorker,
    _emitted_stream_target,
    capture_emitted_segment,
)
from civiccast.egress.models import EgressConfig, EgressSinkSpec, EgressStateRow
from civiccast.egress.store import InMemoryEgressStore
from civiccast.stream._ffmpeg import FfmpegNotFoundError

_SRT = "1\n00:00:00,200 --> 00:00:01,500\nHELLO CIVICCAST\n"


def _cue(text: str = "HELLO CIVICCAST") -> CaptionCue:
    return CaptionCue(
        cue_id="c1",
        start_seconds=0.2,
        end_seconds=1.5,
        text=text,
        confidence=1.0,
        low_confidence=False,
    )


def _runner(stdout: str = "", returncode: int = 0) -> Any:
    return lambda _args: SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _clock() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _worker(
    store: InMemoryEgressStore, *, expected, runner, segment: Path | None
) -> CaptionProofWorker:
    return CaptionProofWorker(
        store=store,
        on_air_channels=lambda: ["gov"],
        capture_segment=lambda _ch: segment,
        expected_cues_provider=lambda _ch: expected,
        runner=runner,
        clock=_clock,
    )


def test_worker_persists_pass_for_matching_captions(tmp_path) -> None:
    store = InMemoryEgressStore()
    seg = tmp_path / "seg.ts"
    seg.write_bytes(b"x")
    worker = _worker(store, expected=[_cue()], runner=_runner(_SRT), segment=seg)
    result = worker.run_once()
    assert result.passed == 1
    assert result.failed == 0
    sample = store.latest_caption_proof_sample("gov")
    assert sample is not None
    assert sample.status == "PASS"
    assert sample.caption_status == "on"
    assert sample.mode == "cea-708"


def _video_pts_packet(seconds: float) -> bytes:
    pts = round(seconds * 90000) % (1 << 33)
    encoded = bytes(
        [
            0x21 | ((pts >> 29) & 14),
            (pts >> 22) & 255,
            1 | ((pts >> 14) & 254),
            (pts >> 7) & 255,
            1 | ((pts << 1) & 254),
        ]
    )
    pes = b"\0\0\x01\xe0\0\0\x80\x80\x05" + encoded
    return b"\x47\x41\x00\x10" + pes + b"\xff" * (184 - len(pes))


def test_proof_uses_real_capture_pts_and_keeps_missing_and_wrong_cues_detectable(tmp_path):
    from civiccast.egress.gst.caption_clock import TS_CLOCK_WRAP_SECONDS

    seg = tmp_path / "seg.ts"
    # 36-hour pipeline plus the mux's documented one-hour transport epoch.
    origin = 36 * 3600 + 3600
    seg.write_bytes(_video_pts_packet(origin) + _video_pts_packet(origin + 6))
    expected = [
        _cue().model_copy(update={"start_seconds": origin + 0.2, "end_seconds": origin + 1.5})
    ]
    decoded_origin = origin % TS_CLOCK_WRAP_SECONDS
    from civiccast.captions.webvtt import render_webvtt

    decoded = render_webvtt(
        [
            _cue().model_copy(
                update={
                    "start_seconds": decoded_origin + 0.2,
                    "end_seconds": decoded_origin + 1.5,
                }
            )
        ]
    )
    store = InMemoryEgressStore()
    worker = _worker(store, expected=expected, runner=_runner(decoded), segment=seg)
    worker._align_transport_clock = True
    assert worker.run_once().passed == 1
    worker._runner = _runner("")
    assert worker.run_once().failed == 1
    worker._runner = _runner(decoded.replace("HELLO CIVICCAST", "WRONG CAPTION"))
    assert worker.run_once().failed == 1
    # A partial boundary page cannot establish a PASS by itself.
    worker._expected_cues_provider = lambda _ch: [
        expected[0].model_copy(
            update={
                "start_seconds": origin - 1,
                "end_seconds": origin + 1,
            }
        )
    ]
    worker._runner = _runner(decoded)
    assert worker.run_once().failed == 1
    assert store.latest_caption_proof_sample("gov").expected_cue_count == 0


def test_worker_fails_when_captions_do_not_survive(tmp_path) -> None:
    store = InMemoryEgressStore()
    seg = tmp_path / "seg.ts"
    seg.write_bytes(b"x")
    # decode-back returns no captions (empty stdout) → FAIL, fail-closed not-verified
    worker = _worker(store, expected=[_cue()], runner=_runner(""), segment=seg)
    result = worker.run_once()
    assert result.passed == 0
    assert result.failed == 1
    sample = store.latest_caption_proof_sample("gov")
    assert sample.status == "FAIL"
    assert sample.caption_status == "not-verified"


def test_worker_records_unavailable_without_running_decoder_when_no_segment_captured() -> None:
    store = InMemoryEgressStore()
    calls = []
    worker = _worker(store, expected=[_cue()], runner=lambda args: calls.append(args), segment=None)
    result = worker.run_once()
    assert result.skipped_no_capture == 1
    assert result.scanned == 0
    sample = store.latest_caption_proof_sample("gov")
    assert sample is not None
    assert sample.sampled_at == _clock()
    assert sample.status == "FAIL"
    assert sample.caption_status == "not-verified"
    assert sample.decoder_name == "not-run"
    assert sample.blocker == "EGRESS_CAPTION_DECODE_BACK_CAPTURE_UNAVAILABLE"
    assert calls == []


def test_worker_unavailable_capture_replaces_prior_pass(tmp_path) -> None:
    store = InMemoryEgressStore()
    segment = tmp_path / "segment.ts"
    segment.write_bytes(b"x")
    worker = _worker(store, expected=[_cue()], runner=_runner(_SRT), segment=segment)
    assert worker.run_once().passed == 1
    worker._capture_segment = lambda _ch: None
    assert worker.run_once().skipped_no_capture == 1
    assert store.latest_caption_proof_sample("gov").caption_status == "not-verified"


@pytest.mark.parametrize("error", [OSError("capture unavailable"), FfmpegNotFoundError("missing")])
def test_worker_records_capture_error_and_continues_with_later_channel(tmp_path, error) -> None:
    store = InMemoryEgressStore()
    segment = tmp_path / "segment.ts"
    segment.write_bytes(b"x")

    def capture(channel):
        if channel == "gov":
            raise error
        return segment

    worker = CaptionProofWorker(
        store=store,
        on_air_channels=lambda: ["gov", "edu"],
        capture_segment=capture,
        expected_cues_provider=lambda _ch: [_cue()],
        runner=_runner(_SRT),
        clock=_clock,
    )
    result = worker.run_once()
    assert result.skipped_no_capture == 1
    assert result.passed == 1
    assert store.latest_caption_proof_sample("gov").decoder_name == "not-run"
    assert store.latest_caption_proof_sample("gov").sampled_at == _clock()
    assert store.latest_caption_proof_sample("edu").status == "PASS"


def test_worker_does_not_swallow_unexpected_capture_bugs() -> None:
    worker = _worker(InMemoryEgressStore(), expected=[], runner=_runner(), segment=None)

    def capture(_channel):
        raise AssertionError("capture bug")

    worker._capture_segment = capture
    with pytest.raises(AssertionError, match="capture bug"):
        worker.run_once()


def test_worker_fail_closed_when_no_expected_cues(tmp_path) -> None:
    store = InMemoryEgressStore()
    seg = tmp_path / "seg.ts"
    seg.write_bytes(b"x")
    # even if the stream HAS captions, no expected cues never fabricates a PASS
    worker = _worker(store, expected=[], runner=_runner(_SRT), segment=seg)
    result = worker.run_once()
    assert result.failed == 1
    sample = store.latest_caption_proof_sample("gov")
    assert sample.status == "FAIL"
    assert sample.caption_status == "not-verified"
    assert sample.blocker == "EGRESS_CAPTION_DECODE_BACK_NO_EXPECTED_CUES"


# --- live-capture target selection (the WSL/LPM edge's pure parts) --------------


def _config(*sinks: EgressSinkSpec) -> EgressConfig:
    return EgressConfig(channel_id="gov", enabled=True, slate_message="x", sinks=list(sinks))


def test_emitted_stream_target_prefers_file_sink() -> None:
    store = InMemoryEgressStore()
    store.upsert_config(
        _config(
            EgressSinkSpec(kind="udp-ts", label="head", uri="udp://239.0.0.9:5000"),
            EgressSinkSpec(kind="file", label="cap", uri="file:///tmp/gov.ts"),
        )
    )
    assert _emitted_stream_target(store, "gov") == ("/tmp/gov.ts", True)


def test_emitted_stream_target_falls_back_to_udp() -> None:
    store = InMemoryEgressStore()
    store.upsert_config(
        _config(EgressSinkSpec(kind="udp-ts", label="head", uri="udp://239.0.0.9:5000"))
    )
    assert _emitted_stream_target(store, "gov") == ("udp://239.0.0.9:5000", False)


@pytest.mark.parametrize("file_uri", [False, True])
def test_hls_capture_reads_advertised_manifest_with_bounded_preserved_clock(
    tmp_path, file_uri
) -> None:
    store = InMemoryEgressStore()
    hls_dir = tmp_path / ("served-hls" if file_uri else "served hls")
    uri = hls_dir.as_uri() if file_uri else str(hls_dir)
    store.upsert_config(_config(EgressSinkSpec(kind="hls", label="local", uri=uri)))
    manifest = str(hls_dir / "playlist.m3u8")
    assert _emitted_stream_target(store, "gov") == (manifest, False)
    hls_dir.mkdir()
    Path(manifest).write_text("#EXTM3U\n#EXTINF:2,\nseg000000001.ts\n")
    seen = {}

    def capture(args, *, timeout):
        seen.update(args=args, timeout=timeout)
        Path(args[-1]).write_bytes(b"captured transport")
        Path(manifest).write_text("#EXTM3U\n#EXTINF:2,\nseg000000002.ts\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = capture_emitted_segment(store, "gov", work_dir=tmp_path, runner=capture)
    assert result is not None
    args = seen["args"]
    assert args[args.index("-i") + 1] == manifest
    assert "playlist.mux.m3u8" not in " ".join(args)
    assert "-sseof" not in args
    assert args.index("-re") < args.index("-i")
    assert args[args.index("-t") + 1] == f"{DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS:g}"
    assert args.index("-t") < args.index("-i")
    assert "-copyts" in args
    assert args[args.index("-mpegts_copyts") + 1] == "1"
    assert args[args.index("-c") + 1] == "copy"
    assert seen["timeout"] == DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS


def test_hls_capture_reads_the_whole_window_so_the_duration_limit_cannot_be_spent_before_reading(
    tmp_path,
) -> None:
    # RC4 host run 2026-11-04: the shipped FFmpeg returned exit 0 and ZERO bytes for
    # `-t 6 -i playlist.m3u8` when it joined the live window at its default point (3
    # segments from the end, 7.5 s into a 15 s window), so every proof sample was
    # "capture unavailable". Starting at the first listed segment captured 6 s 3/3.
    store = InMemoryEgressStore()
    hls_dir = tmp_path / "hls"
    store.upsert_config(_config(EgressSinkSpec(kind="hls", label="local", uri=str(hls_dir))))
    hls_dir.mkdir()
    manifest = hls_dir / "playlist.m3u8"
    manifest.write_text("#EXTM3U\n#EXTINF:2,\nseg000000001.ts\n")
    seen = {}

    def capture(args, *, timeout):
        seen["args"] = args
        Path(args[-1]).write_bytes(b"captured transport")
        manifest.write_text("#EXTM3U\n#EXTINF:2,\nseg000000002.ts\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert capture_emitted_segment(store, "gov", work_dir=tmp_path, runner=capture) is not None
    args = seen["args"]
    assert args[args.index("-live_start_index") + 1] == "0"
    assert args.index("-live_start_index") < args.index("-i")


def test_live_start_index_is_hls_only(tmp_path) -> None:
    # The option belongs to FFmpeg's HLS demuxer; a udp/srt input must not get it.
    store = InMemoryEgressStore()
    store.upsert_config(
        _config(EgressSinkSpec(kind="udp-ts", label="head", uri="udp://239.0.0.9:5000"))
    )
    seen = {}

    def capture(args, *, timeout):
        seen["args"] = args
        Path(args[-1]).write_bytes(b"captured transport")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert capture_emitted_segment(store, "gov", work_dir=tmp_path, runner=capture) is not None
    assert "-live_start_index" not in seen["args"]


def test_hls_frozen_window_never_reuses_captions_as_fresh_proof(tmp_path) -> None:
    store = InMemoryEgressStore()
    hls_dir = tmp_path / "hls"
    hls_dir.mkdir()
    manifest = hls_dir / "playlist.m3u8"
    manifest.write_text("#EXTM3U\n#EXTINF:2,\nseg000000001.ts\n")
    store.upsert_config(_config(EgressSinkSpec(kind="hls", label="local", uri=str(hls_dir))))
    calls = []

    def capture(args, *, timeout):
        calls.append(args)
        Path(args[-1]).write_bytes(b"old captioned transport")
        # Fresh manifest mtime and changed tags cannot hide frozen media.
        manifest.write_text(
            f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{len(calls)}\n#EXTINF:2,\nseg000000001.ts\n"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    for _ in range(2):
        assert capture_emitted_segment(store, "gov", work_dir=tmp_path, runner=capture) is None
        assert not (tmp_path / "gov" / "caption-proof" / "segment.ts").exists()
    assert len(calls) == 2


def test_emitted_stream_target_none_for_non_ts_sinks() -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config(EgressSinkSpec(kind="sdi", label="sdi", uri="decklink://0")))
    assert _emitted_stream_target(store, "gov") is None
    assert _emitted_stream_target(store, "missing") is None


def test_capture_returns_none_when_runner_produces_no_bytes(tmp_path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(
        _config(EgressSinkSpec(kind="udp-ts", label="head", uri="udp://239.0.0.9:5000"))
    )
    # runner "succeeds" but writes nothing → None (so the channel is skipped, not a false PASS)
    captured = capture_emitted_segment(store, "gov", work_dir=tmp_path, runner=_runner(""))
    assert captured is None


def test_capture_passes_a_bounded_timeout_and_fails_closed_on_timeout(tmp_path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(
        _config(EgressSinkSpec(kind="udp-ts", label="head", uri="udp://239.0.0.9:5000"))
    )
    seen: dict[str, object] = {}

    def timed_out_runner(args: list[str], *, timeout: float) -> Any:
        seen["args"] = args
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(args, timeout)

    captured = capture_emitted_segment(
        store,
        "gov",
        work_dir=tmp_path,
        runner=timed_out_runner,
    )

    assert captured is None
    assert seen["timeout"] == DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS
    assert not (tmp_path / "gov" / "caption-proof" / "segment.ts").exists()


def test_on_air_filter_via_store_state() -> None:
    # the wired on_air filter only samples ON_AIR channels
    store = InMemoryEgressStore()
    store.upsert_config(_config(EgressSinkSpec(kind="file", label="f", uri="file:///tmp/gov.ts")))
    store.write_state(
        EgressStateRow(
            channel_id="gov", state="STOPPED", updated_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    )
    on_air = [
        c.channel_id
        for c in store.list_configs()
        if (row := store.read_state(c.channel_id)) is not None and row.state == "ON_AIR"
    ]
    assert on_air == []


def test_worker_is_idle_while_live_captions_are_switched_off(tmp_path) -> None:
    # Round-2 review MAJOR 3: with the operator switch off there is no embed leg to
    # prove, so the loop must neither capture (a 6 s ffmpeg run per channel) nor
    # persist a guaranteed NO_EXPECTED_CUES FAIL row every 30 s.
    store = InMemoryEgressStore()
    captures: list[str] = []
    seg = tmp_path / "seg.ts"
    seg.write_bytes(b"x")

    def _capture(channel_id: str) -> Path:
        captures.append(channel_id)
        return seg

    worker = CaptionProofWorker(
        store=store,
        on_air_channels=lambda: ["gov"],
        capture_segment=_capture,
        expected_cues_provider=lambda _ch: [],
        runner=_runner(""),
        clock=_clock,
        is_enabled=lambda: False,
    )

    result = worker.run_once()

    assert result.scanned == 0
    assert captures == []
    assert store.latest_caption_proof_sample("gov") is None


def test_worker_resumes_when_live_captions_are_switched_on_without_a_restart(
    tmp_path,
) -> None:
    store = InMemoryEgressStore()
    seg = tmp_path / "seg.ts"
    seg.write_bytes(b"x")
    enabled = [False]
    worker = CaptionProofWorker(
        store=store,
        on_air_channels=lambda: ["gov"],
        capture_segment=lambda _ch: seg,
        expected_cues_provider=lambda _ch: [_cue()],
        runner=_runner(_SRT),
        clock=_clock,
        is_enabled=lambda: enabled[0],
    )
    assert worker.run_once().scanned == 0

    enabled[0] = True  # the switch is re-read every scan
    result = worker.run_once()

    assert result.passed == 1
    sample = store.latest_caption_proof_sample("gov")
    assert sample is not None and sample.status == "PASS"
