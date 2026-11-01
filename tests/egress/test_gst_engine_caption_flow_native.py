# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Real-runtime caption scheduling regression, separate from reload retirement."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.egress import test_gst_engine_wsl as native

pytestmark = pytest.mark.skipif(
    not (native._wsl_gi_available() and native._cc_embed_elements_available()),
    reason="caption flow proof requires the real bundled GStreamer caption runtime",
)


def test_restarted_worker_records_actual_startup_caption_display(tmp_path: Path) -> None:
    """A pre-existing audio boundary must not orphan startup timing probes."""
    tap_dir = tmp_path / "tap"
    tap_dir.mkdir()
    (tap_dir / "program-boundary.json").write_text(
        json.dumps({"token": "previous-worker", "first_segment": 0})
    )
    output = tmp_path / "startup-caption.ts"
    graph = replace(
        native._filesink_graph(native._av_demo_graph(), output),
        captions=native.graphmod.caption_embed_leg_live(),
        audio_tap=native.graphmod.AudioTapLeg(tap_dir=str(tap_dir), segment_seconds=0.5),
    )
    proc, control, log = native._launch_worker(tmp_path, graph, output)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            boundary = json.loads((tap_dir / "program-boundary.json").read_text())
            if boundary["token"] != "previous-worker":
                break
            assert proc.poll() is None, log.read_text()
            time.sleep(0.05)
        else:
            pytest.fail("restarted worker did not publish its startup boundary")
        payload = base64.b64encode(b"STARTUP DISPLAY CLOCK").decode("ascii")
        native._send(control, f"caption 0 1500 {payload} {boundary['token']}")
        time.sleep(5)
        native._send(control, "stop")
        proc.wait(timeout=10)
    finally:
        native._reap(proc)
    assert proc.returncode == 0, log.read_text()
    presentation = json.loads((tmp_path / "captions" / "presentation.json").read_text())
    assert presentation["encoded_page_count"] == presentation["displayed_page_count"] == 1
    assert presentation["pages"][0]["text"] == "STARTUP DISPLAY CLOCK"
    assert presentation["errors"] == []
    native._assert_caption_decoded(
        native._decode_back_caption_text(output), "STARTUP DISPLAY CLOCK", context=log.read_text()
    )


def test_scheduled_program_prerolls_on_live_slate_without_early_selection(tmp_path):
    """The real A/V first buffers remain held until the scheduled start."""
    from gi.repository import GLib

    from civiccast.egress.gst.engine import GstPlayoutEngine
    from civiccast.egress.gst.graph import graph_from_json

    output = tmp_path / "scheduled-start.ts"
    initial = native._filesink_graph(native._av_demo_graph(), output)
    initial = graph_from_json(native.graphmod.graph_to_json(initial))
    incoming = graph_from_json(
        native.graphmod.graph_to_json(native._segment_timed_reload_graph())
    ).sources[0]
    engine = GstPlayoutEngine(initial)
    observations, settlements, failures = [], [], []
    timing = {}

    def settled(committed, reason):
        settlements.append((committed, reason, time.time()))
        GLib.timeout_add(300, lambda: engine._loop.quit() or False)

    def arm():
        timing["due"] = time.time() + 2
        engine.reload_program(
            incoming,
            switch_at_end_of_current=True,
            switch_at_unix=timing["due"],
            on_settled=settled,
        )
        return False

    def observe():
        pending = engine._pending_reload
        if pending is not None and pending["new_leg_ready"] and time.time() < timing["due"]:
            observations.append(len(pending["hold_probes"]))
            if pending.get("commit_in_progress"):
                failures.append("program selected before due")
        return not settlements

    def deadline():
        failures.append("scheduled start did not settle")
        engine._loop.quit()
        return False

    timers = [
        GLib.timeout_add(200, arm),
        GLib.timeout_add(50, observe),
        GLib.timeout_add_seconds(10, deadline),
    ]
    try:
        result = engine.run_forever()
    finally:
        for timer in timers:
            with native.contextlib.suppress(Exception):
                GLib.source_remove(timer)
        engine.stop()
    assert not failures, (failures, result)
    assert observations and set(observations) == {2}, (
        "both real A/V buffers must preroll before the slot"
    )
    assert len(settlements) == 1 and settlements[0][0], settlements
    assert timing["due"] <= settlements[0][2] < timing["due"] + 3
    assert result["error"] is None and result["teardown_clean"]
    assert output.stat().st_size > 0


def _packet_span_diagnostic(path: Path) -> dict:
    """Inspect retained real packets after a worker failure, off the media path."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_packets",
                "-show_entries",
                "packet=codec_type,pts_time,duration_time",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        packets = json.loads(result.stdout)["packets"]
        spans = {}
        for kind in ("video", "audio"):
            selected = [p for p in packets if p.get("codec_type") == kind and "pts_time" in p]
            if selected:
                start = min(float(p["pts_time"]) for p in selected)
                end = max(float(p["pts_time"]) + float(p.get("duration_time", 0)) for p in selected)
                spans[kind] = {
                    "packets": len(selected),
                    "start": start,
                    "end": end,
                    "span": end - start,
                }
        return {"bytes": path.stat().st_size, "streams": spans}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        return {"diagnostic_error": str(exc)}


def test_live_caption_queue_probe_releases_real_event_reservations(tmp_path: Path) -> None:
    """Prove the real queue-source probe, not merely the GI-free gate in isolation."""
    from civiccast.egress.gst.engine import GstPlayoutEngine

    graph = native._filesink_graph(
        replace(native._av_demo_graph(), captions=native.graphmod.caption_embed_leg_live()),
        tmp_path / "caption-flow.ts",
    )
    engine = GstPlayoutEngine(graph)
    gate = engine._caption_gap_gate
    reserve = Mock(wraps=gate.reserve)
    forwarded = Mock(wraps=gate.entered_downstream)
    gate.reserve = reserve
    gate.entered_downstream = forwarded
    try:
        queue = engine.pipeline.get_by_name("caption_flow_queue")
        assert queue is not None
        assert queue.get_property("max-size-buffers") == 200
        assert queue.get_property("max-size-bytes") == 10_485_760
        assert queue.get_property("max-size-time") == 1_000_000_000
        assert int(queue.get_property("leaky")) == 0
        result = engine.run(swaps=0, interval_s=1)
        assert result["error"] is None, result
        assert result["teardown_clean"] is True, result
        reserved = {call.args[0] for call in reserve.call_args_list}
        observed = {call.args[0] for call in forwarded.call_args_list}
        assert len(observed) >= 2, "prime and heartbeat never traversed the real queue probe"
        assert observed <= reserved, "a forwarded GAP lacked its prior admission reservation"
        assert gate.pending is None
        assert gate.reserve(12345) is False, "stop must leave GAP admissions closed"
        assert engine._caption_gap_probe is None
        assert engine._caption_gap_heartbeat_id is None
    finally:
        engine.stop()


def test_internal_playlist_change_clears_old_captions_in_emitted_output(tmp_path: Path) -> None:
    """No reload command: actual concat boundaries must change caption owners."""
    from gi.repository import GLib, Gst

    from civiccast.egress.gst.engine import GstPlayoutEngine
    from civiccast.egress.gst.graph import graph_from_json

    clips = [tmp_path / f"program-{i}.ts" for i in range(3)]
    for clip in clips:
        native._write_short_av_ts_clip(clip, seconds=2.5, video_caps=native._PRODUCTION_CAPS)
    output = tmp_path / "internal-caption-boundaries.ts"
    graph = native._production_pressure_playlist_graph(
        clips, out_ts=output, audio_tap_dir=tmp_path / "tap"
    )
    program = replace(
        graph.sources[0],
        subchain_sources=tuple(
            {
                "kind": "program" if i < 2 else "cg",
                "asset_id": f"asset-{i}",
                "label": f"Program {i}",
                "schedule_item_id": "repeat-row",
                "schedule_occurrence_start": f"2026-10-30T00:00:0{i}+00:00",
            }
            for i in range(3)
        ),
    )
    # The shared subprocess helpers load graph.py in standalone mode; use the
    # same JSON boundary as a real worker before constructing a package engine.
    graph = graph_from_json(
        native.graphmod.graph_to_json(replace(graph, sources=(program, *graph.sources[1:])))
    )
    engine = GstPlayoutEngine(graph)
    original = engine._finish_caption_program
    boundaries = []

    def finish(token, cutoff, first_segment):
        result = original(token, cutoff, first_segment)
        if token != engine._caption_program_token:
            return result
        if boundaries:
            assert not engine.push_caption_cue(
                text="STALE SPEECH",
                pts_seconds=0,
                duration_seconds=10,
                program_token=boundaries[-1][0],
            )
        boundaries.append((token, cutoff))
        if len(boundaries) <= 2:
            assert engine.push_caption_cue(
                text=("FIRST PROGRAM", "SECOND PROGRAM")[len(boundaries) - 1],
                pts_seconds=cutoff / Gst.SECOND,
                duration_seconds=10,
                program_token=token,
            )
        return result

    engine._finish_caption_program = finish
    expired = []

    def timeout():
        expired.append(True)
        engine._loop.quit()
        return False

    timer = GLib.timeout_add_seconds(20, timeout)
    try:
        result = engine.run_forever()
    finally:
        if not expired:
            GLib.source_remove(timer)
        engine.stop()
    assert not expired, "internal caption boundary stalled finite playback"
    assert result["error"] is None, result
    assert result["teardown_clean"] is True, result
    assert len(boundaries) == 3
    decoded = native._decode_back_caption_text(output)
    native._assert_caption_decoded(decoded, "FIRST PROGRAM", context=str(result))
    native._assert_caption_decoded(decoded, "SECOND PROGRAM", context=str(result))
    assert "STALE SPEECH" not in decoded
    for text, (_, next_cutoff) in zip(
        ("FIRST PROGRAM", "SECOND PROGRAM"), boundaries[1:], strict=True
    ):
        blocks = [b for b in decoded.split("\n\n") if text in b]
        for block in blocks:
            match = re.search(r"--> (\d+):(\d+):(\d+),(\d+)", block)
            assert match is not None, block
            h, m, s, ms = map(int, match.groups())
            assert h * 3600 + m * 60 + s + ms / 1000 <= next_cutoff / Gst.SECOND + 1 / 30, block


def test_three_captioned_workers_finish_complex_playlist_without_reload(tmp_path: Path) -> None:
    """Keep caption embed and rolling audio capture on during noisy video.

    The exact beta.5 baseline intermittently stopped delivering caption GAPs
    and stalled output without any reload command. Repeat three concurrent
    channels three times: ordinary bars alternate with high-complexity snow.
    No debug logging, artificial sleeps in the media path, or disabled captions
    may substitute for successful complete playback.
    """
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is required to verify complete A/V capture")
    clips = [tmp_path / f"segment-{index}.ts" for index in range(4)]
    for index, clip in enumerate(clips):
        native._write_short_av_ts_clip(
            clip, seconds=2.5, pattern=(0, 1)[index % 2], video_caps=native._PRODUCTION_CAPS
        )

    for attempt in range(3):
        workers = []
        try:
            for index in range(3):
                channel = tmp_path / f"attempt-{attempt + 1}-channel-{index + 1}"
                channel.mkdir()
                capture = channel / "output.ts"
                tap = channel / "caption-tap"
                graph = native._production_pressure_playlist_graph(
                    clips, out_ts=capture, audio_tap_dir=tap
                )
                process, control, log = native._launch_worker(channel, graph, capture)
                workers.append((process, control, log, capture, tap))
            for process, _control, log, capture, tap in workers:
                code = process.wait(timeout=40)
                output = log.read_text(encoding="utf-8", errors="replace")
                if code != 0:
                    diagnostic = {
                        "cpu_count": os.cpu_count(),
                        "failed_channel": capture.parent.name,
                        "exit_code": code,
                        "media": {
                            str(p.relative_to(tmp_path)): _packet_span_diagnostic(p)
                            for p in [*clips, capture]
                        },
                    }
                    (tmp_path / "failure-diagnostic.json").write_text(
                        json.dumps(diagnostic, indent=2), encoding="utf-8"
                    )
                    # Preserve actual source clips, worker logs and TS captures;
                    # a later local pass cannot explain this runner's failure.
                    workspace = os.environ.get("GITHUB_WORKSPACE")
                    if workspace:
                        try:
                            retained = Path(workspace) / "caption-worker-failure"
                            retained.mkdir(exist_ok=True)
                            for source in [*clips, tmp_path / "failure-diagnostic.json"]:
                                shutil.copy2(source, retained / source.name)
                            shutil.copytree(
                                capture.parent, retained / capture.parent.name, dirs_exist_ok=True
                            )
                            for _process, _control, worker_log, _capture, _tap in workers:
                                shutil.copy2(
                                    worker_log, retained / (worker_log.parent.name + ".log")
                                )
                        except OSError as exc:
                            diagnostic["artifact_copy_error"] = str(exc)
                    output += "\nPacket timing at failure: " + json.dumps(diagnostic)
                assert code == 0, output
                assert "CTRL stall:" not in output, output
                assert "'teardown_clean': True" in output, output
                native._assert_continuous(capture, output, require_audio_pid=True)
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "packet=codec_type,pts_time,duration_time",
                        "-show_packets",
                        "-of",
                        "json",
                        str(capture),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                # TS stream metadata may legitimately omit audio duration. Measure
                # the actual timestamped packet span of EACH elementary stream.
                packets = json.loads(probe.stdout)["packets"]
                for kind in ("video", "audio"):
                    selected = [packet for packet in packets if packet["codec_type"] == kind]
                    assert selected, f"no {kind} packets"
                    starts = [float(packet["pts_time"]) for packet in selected]
                    ends = [
                        float(packet["pts_time"]) + float(packet["duration_time"])
                        for packet in selected
                    ]
                    span = max(ends) - min(starts)
                    assert span >= 9.5, f"{kind} truncated to {span:.3f}s"
                assert list(tap.glob("chunk-*.wav")), output
                assert not list(tap.glob("*.partial")), output
        finally:
            for process, *_rest in workers:
                native._reap(process)
