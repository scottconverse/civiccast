# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Assert the production prime/heartbeat path actually uses GAP admission."""

from __future__ import annotations

from itertools import count
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from civiccast.egress.gst.caption_flow import CaptionGapGate
from tests.egress.test_gst_engine_reload_commit_ordering import engine_module  # noqa: F401


@pytest.fixture
def caption_engine(engine_module: Any) -> Any:  # noqa: F811
    sequence = count(100)

    def new_gap(start: int, duration: int) -> Any:
        seq = next(sequence)
        return SimpleNamespace(start=start, duration=duration, get_seqnum=lambda: seq)

    engine_module.Gst.MSECOND = 1
    engine_module.Gst.Event = SimpleNamespace(new_gap=new_gap)
    engine = engine_module.GstPlayoutEngine.__new__(engine_module.GstPlayoutEngine)
    engine._caption_gap_gate = CaptionGapGate()
    engine._caption_stream_position_ms = 0
    engine._pipeline_running_time_ms = lambda: 1000
    engine._stopping = False
    engine._error = None
    engine._loop = SimpleNamespace(quit=Mock())
    engine.caption_appsrc = SimpleNamespace(send_event=Mock(return_value=True))
    return engine


def test_prime_reserves_before_enqueue_and_retains_initial_gap(caption_engine: Any) -> None:
    def send(event: Any) -> bool:
        assert caption_engine._caption_gap_gate.pending == event.get_seqnum()
        assert (event.start, event.duration) == (0, 250)
        return True

    caption_engine.caption_appsrc.send_event.side_effect = send
    caption_engine._prime_live_caption_stream()
    assert caption_engine._caption_stream_position_ms == 250
    assert caption_engine._caption_gap_gate.pending is not None


def test_delivery_receipt_uses_accepted_rebased_pts_only(caption_engine, engine_module, tmp_path):  # noqa: F811
    import json

    from civiccast.egress.gst.caption_clock import CaptionDeliveryClock

    engine_module.Gst.Buffer = SimpleNamespace(
        new_allocate=lambda *args: SimpleNamespace(fill=Mock())
    )
    engine_module.Gst.FlowReturn = SimpleNamespace(OK=0)
    caption_engine.caption_appsrc.emit = Mock(return_value=0)
    receipt = tmp_path / "delivered.json"
    caption_engine.caption_delivery_clock = CaptionDeliveryClock(receipt)
    assert caption_engine.push_caption_cue(text="HELLO", pts_seconds=36 * 3600, duration_seconds=2)
    pages = json.loads(receipt.read_text())
    assert pages == [{"text": "HELLO", "start_seconds": 3601.25, "end_seconds": 3603.25}]
    caption_engine.caption_appsrc.emit.return_value = -1
    assert not caption_engine.push_caption_cue(
        text="REJECTED", pts_seconds=36 * 3600, duration_seconds=2
    )
    assert json.loads(receipt.read_text()) == pages


def test_heartbeat_does_not_enqueue_or_advance_while_gap_waits(caption_engine: Any) -> None:
    assert caption_engine._caption_gap_gate.reserve(99)
    caption_engine._caption_stream_position_ms = 250
    assert caption_engine._advance_live_caption_gap() is True
    caption_engine.caption_appsrc.send_event.assert_not_called()
    assert caption_engine._caption_stream_position_ms == 250


def test_late_clock_attachment_observes_startup_program_after_audio_tap_fence(
    caption_engine: Any,
    engine_module: Any,  # noqa: F811
    tmp_path: Any,
) -> None:
    """Production builds the graph before attaching its delivery clock."""
    import json

    from civiccast.egress.gst.caption_clock import CaptionDeliveryClock

    source, encoded = Mock(), Mock()
    caption_engine.caption_appsrc = source
    caption_engine._caption_source_elements = [source, encoded]
    caption_engine._caption_program_token = "restarted-worker-program"
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    caption_engine.set_caption_delivery_clock(clock)
    engine_module.Gst.SECOND = 1_000_000_000
    engine_module.Gst.CLOCK_TIME_NONE = -1

    def observe(element: Any, data: bytes, pts: int) -> None:
        callback = element.get_static_pad.return_value.add_probe.call_args.args[1]
        buffer = SimpleNamespace(
            pts=pts,
            get_size=lambda: len(data),
            extract_dup=lambda *_: data,
        )
        callback(
            None,
            SimpleNamespace(type=engine_module.Gst.PadProbeType.BUFFER, get_buffer=lambda: buffer),
        )

    observe(source, b"STARTUP PAGE", 4_000_000_000)
    observe(encoded, b"\xfc\x94\x20\xfc\x94\x2f", 5_000_000_000)
    observe(encoded, b"\xfc\x94\x2c", 7_000_000_000)
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == [{"text": "STARTUP PAGE", "start_seconds": 3605, "end_seconds": 3607}]
    assert result["encoded_page_count"] == result["displayed_page_count"] == 1
    assert result["errors"] == []


def test_heartbeat_send_failure_clears_own_reservation_and_quits(caption_engine: Any) -> None:
    caption_engine.caption_appsrc.send_event.return_value = False
    assert caption_engine._advance_live_caption_gap() is False
    assert caption_engine._caption_gap_gate.pending is None
    assert caption_engine._caption_stream_position_ms == 0
    assert caption_engine._error[0] == "caption-gap"
    caption_engine._loop.quit.assert_called_once()


def test_fast_queue_probe_does_not_get_replaced_by_sender(caption_engine: Any) -> None:
    def send(event: Any) -> bool:
        assert caption_engine._caption_gap_gate.entered_downstream(event.get_seqnum())
        return True

    caption_engine.caption_appsrc.send_event.side_effect = send
    assert caption_engine._advance_live_caption_gap() is True
    assert caption_engine._caption_gap_gate.pending is None
    assert caption_engine._caption_stream_position_ms == 1250


def test_heartbeat_flushes_scheduled_erase_before_video_reaches_it(caption_engine):
    caption_engine._caption_stream_position_ms = 1200
    assert caption_engine._advance_live_caption_gap() is True
    event = caption_engine.caption_appsrc.send_event.call_args.args[0]
    assert (event.start, event.duration) == (1200, 50)
    assert caption_engine._caption_stream_position_ms == 1250


def test_stopped_engine_does_not_enqueue_more_heartbeats(caption_engine: Any) -> None:
    caption_engine._stopping = True
    caption_engine._caption_gap_gate.close()
    assert caption_engine._advance_live_caption_gap() is False
    caption_engine.caption_appsrc.send_event.assert_not_called()
