# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Item 88 (measured in sandbox run 17, soak-a6d7871-20260906-213332Z, Opus
diagnosis): the caption-audio-tap fork's ``queue`` element used to be plain
(default, NON-leaky) and its ``appsink`` used ``drop: False`` -- together
capable of backing up the tee it forks from, starving the mux's audio pad
and stopping real TS output. This file proves the graph-side half of the
fix: ``GstPlayoutEngine._audio_tap_element_specs`` (a pure, gi-free function
-- see its definition) builds a queue with ``leaky=2``
(``GST_QUEUE_LEAK_DOWNSTREAM``), a deep buffer cap, and an appsink with
``drop=True``, in the same element order as before.

Uses the same fake-``gi``/``Gst`` fixture as
``test_gst_engine_first_output_timeout.py`` and
``test_gst_engine_preroll_timeout.py`` -- no real GStreamer install needed,
since ``ElementSpec`` is a plain dataclass and this function never
constructs an actual ``Gst.Element``.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from unittest.mock import Mock

import pytest

from civiccast.egress.gst.audio_tap import RollingWavSegmentWriter, read_program_boundary

_ENGINE_MODULE_NAME = "civiccast.egress.gst.engine"


@pytest.fixture
def engine_module():
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_repository = types.ModuleType("gi.repository")
    fake_glib = types.ModuleType("gi.repository.GLib")
    fake_glib.timeout_add_seconds = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_gst = types.ModuleType("gi.repository.Gst")
    fake_repository.GLib = fake_glib  # type: ignore[attr-defined]
    fake_repository.Gst = fake_gst  # type: ignore[attr-defined]
    fake_gi.repository = fake_repository  # type: ignore[attr-defined]

    patched_names = ("gi", "gi.repository", "gi.repository.GLib", "gi.repository.Gst")
    saved = {name: sys.modules.get(name) for name in (*patched_names, _ENGINE_MODULE_NAME)}
    for name in patched_names:
        sys.modules[name] = {
            "gi": fake_gi,
            "gi.repository": fake_repository,
            "gi.repository.GLib": fake_glib,
            "gi.repository.Gst": fake_gst,
        }[name]
    sys.modules.pop(_ENGINE_MODULE_NAME, None)
    try:
        module = importlib.import_module(_ENGINE_MODULE_NAME)
        yield module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_audio_tap_specs_element_order_is_unchanged(engine_module) -> None:
    specs = engine_module._audio_tap_element_specs()
    assert [spec.factory for spec in specs] == [
        "queue",
        "audioconvert",
        "audioresample",
        "capsfilter",
        "appsink",
    ]
    assert [spec.name for spec in specs] == [
        "caption_audio_tap_queue",
        "caption_audio_tap_convert",
        "caption_audio_tap_resample",
        "caption_audio_tap_caps",
        "caption_audio_tap_sink",
    ]


def test_audio_tap_queue_is_downstream_leaky_with_a_deep_buffer_cap(engine_module) -> None:
    """Item 88: a non-leaky queue could back up the tee once the appsink
    callback fell behind -- ``leaky=2`` (GST_QUEUE_LEAK_DOWNSTREAM) drops the
    OLDEST buffered data instead. ``max-size-buffers=200`` is GStreamer's own
    stock ``queue`` default (kept explicit here, not "deepened"). The real
    fix is ``max-size-time=0``, disabling the stock 1s default -- this tap's
    audio format takes ~4.6s to fill 200 buffers, so leaving the 1s time
    bound in place would have made TIME the first (and far too tight) limit
    reached, not buffer count. ``max-size-bytes`` is deliberately left at
    GStreamer's own stock 10 MB default as a memory guard of last resort --
    this should never actually trigger in normal operation; it is the
    backstop of last resort, not the primary fix (the primary fix is the
    writer thread in ``audio_tap.py``)."""
    specs = engine_module._audio_tap_element_specs()
    queue_spec = specs[0]
    assert queue_spec.factory == "queue"
    assert queue_spec.props["leaky"] == 2
    assert queue_spec.props["max-size-buffers"] == 200
    assert queue_spec.props["max-size-time"] == 0
    assert queue_spec.props["max-size-bytes"] == 10_485_760


def test_audio_tap_appsink_drops_rather_than_blocks(engine_module) -> None:
    """Item 88: the appsink used to be ``drop: False`` -- exactly as capable
    of backing up the tee as the non-leaky queue was. ``drop=True`` bounds
    this sink's own contribution to the same failure mode."""
    specs = engine_module._audio_tap_element_specs()
    appsink_spec = specs[-1]
    assert appsink_spec.factory == "appsink"
    assert appsink_spec.props["drop"] is True
    assert appsink_spec.props["max-buffers"] == 32
    assert appsink_spec.props["emit-signals"] is True
    assert appsink_spec.props["sync"] is False


def test_audio_tap_caps_unchanged_mono_16k_s16le(engine_module) -> None:
    specs = engine_module._audio_tap_element_specs()
    caps_spec = specs[3]
    assert caps_spec.factory == "capsfilter"
    assert caps_spec.props["caps"] == (
        "audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved"
    )


def test_internal_occurrence_fences_before_deferred_publication(engine_module, tmp_path) -> None:
    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine._caption_source_identity = ("slate", None, None, "startup")
    writer = RollingWavSegmentWriter(tmp_path, segment_seconds=0.01, sample_rate_hz=1000)
    engine.audio_tap_writer = writer
    callbacks = []
    engine_module.GLib.idle_add = lambda *args: callbacks.append(args)
    source = {
        "kind": "program",
        "asset_id": "a",
        "schedule_item_id": "row",
        "schedule_occurrence_start": "2026-10-30T00:00:00Z",
        "event_id": "first",
    }
    try:
        engine._select_caption_source(source, 100)
        outgoing = engine._caption_program_token
        assert engine._caption_fence_pending is True
        assert not (tmp_path / "program-boundary.json").exists()
        # A continuation may have a new event marker, but retains its occurrence.
        engine._select_caption_source({**source, "event_id": "horizon"}, 200)
        assert len(callbacks) == 1
        # A repeat is a new owner even with the same asset and schedule row.
        engine._select_caption_source(
            {**source, "schedule_occurrence_start": "2026-10-30T00:00:38Z"}, 300
        )
        incoming = engine._caption_program_token
        assert outgoing != incoming
        writer.write_pcm_s16le(b"\x01\x00" * 10, program_token=outgoing)
        # An older deferred callback cannot publish/reinstall the superseded owner.
        first, *args = callbacks[0]
        assert first(*args) is False
        assert not (tmp_path / "program-boundary.json").exists()
        last, *args = callbacks[1]
        assert last(*args) is False
        assert read_program_boundary(tmp_path) == (incoming, 0)
        assert engine._caption_fence_pending is False
        engine._select_caption_source({"kind": "slate", "event_id": "role-slate"}, 400)
        assert engine._caption_program_token != incoming
        assert len(callbacks) == 3
    finally:
        assert writer.close() == "drained"
    assert not list(tmp_path.glob("chunk-*.wav")), "outgoing callback crossed the occurrence fence"


def test_reload_fence_is_adopted_once_even_for_same_occurrence(engine_module) -> None:
    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine._finish_caption_program = Mock()
    engine._begin_caption_program(100)
    token = engine._caption_program_token
    source = {"kind": "program", "asset_id": "a", "schedule_occurrence_start": "same"}
    engine._caption_source_identity = ("program", "a", None, "same")
    engine._select_caption_source(source, 101)
    assert engine._caption_program_token == token
    assert engine._caption_adopt_next_source is False
    engine_module.GLib.idle_add = Mock()
    engine._select_caption_source({**source, "schedule_occurrence_start": "next"}, 200)
    assert engine._caption_program_token != token
    assert engine_module.GLib.idle_add.call_count == 1


def test_reload_hand_over_survives_a_denied_caption_boundary_publish(
    engine_module, tmp_path
) -> None:
    """F7: the RC4 field stall. A Windows sharing denial on program-boundary.json
    raised out of the reload commit and cost ~15 s of filler slide. The hand-over
    must finish; the boundary is published by a later retry."""
    engine = object.__new__(engine_module.GstPlayoutEngine)
    writer = RollingWavSegmentWriter(tmp_path, segment_seconds=0.01, sample_rate_hz=1000)
    engine.audio_tap_writer = writer
    engine.caption_appsrc = None
    real_publish = writer.publish_program_boundary
    denials = [PermissionError(13, "Access is denied")]

    def flaky_publish(token: str, first_segment: int) -> None:
        if denials:
            raise denials.pop()
        real_publish(token, first_segment)

    writer.publish_program_boundary = flaky_publish  # type: ignore[method-assign]
    retries: list[tuple] = []
    engine_module.GLib.timeout_add = lambda ms, fn, *args: retries.append((ms, fn, args)) or 1
    try:
        engine._begin_caption_program(100)  # must not raise into the reload commit
        token = engine._caption_program_token
        assert not (tmp_path / "program-boundary.json").exists()
        assert len(retries) == 1
        _ms, retry, args = retries[0]
        assert retry(*args) is False  # published; do not repeat
        assert read_program_boundary(tmp_path) == (token, 0)
    finally:
        assert writer.close() == "drained"


def test_audio_ownership_pair_publishes_only_after_writer_fence(engine_module) -> None:
    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine._caption_audio_state = ("outgoing", 100)
    observed = []
    engine.audio_tap_writer = types.SimpleNamespace(
        fence_program=lambda token: observed.append((token, engine._caption_audio_state)) or 7,
    )
    token, first = engine._fence_caption_program(200)
    assert observed == [(token, ("outgoing", 100))]
    assert engine._caption_audio_state == (token, 200)
    assert first == 7


def test_initial_selected_source_adopts_startup_caption_token(engine_module) -> None:
    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine._caption_program_token = ""
    engine._fence_caption_program = Mock(return_value=("next", None))
    engine_module.GLib.idle_add = Mock()
    source = {"kind": "src0", "event_id": "startup"}
    engine._select_caption_source(source, 100)
    assert engine._caption_program_token == ""
    engine._fence_caption_program.assert_not_called()
    engine_module.GLib.idle_add.assert_not_called()
    engine._select_caption_source({"kind": "slate", "event_id": "slate"}, 200)
    engine._fence_caption_program.assert_called_once_with(200)


def test_selected_frame_uses_serialized_identity_without_selector_read(engine_module) -> None:
    gst = engine_module.Gst
    gst.PadProbeType = types.SimpleNamespace(EVENT_DOWNSTREAM=1, BUFFER=2)
    gst.PadProbeReturn = types.SimpleNamespace(OK=0)
    pad = Mock()
    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine.selector = Mock()
    engine.selector.get_static_pad.return_value = pad
    engine.selector.get_property.side_effect = AssertionError("streaming selector lock read")
    engine._measure_first_buffer = Mock(return_value=(None, 123, None))
    engine._pipeline_running_time_ns = Mock(return_value=100)
    gst.SECOND = 1_000_000_000
    engine._select_caption_source = Mock()
    engine._install_source_output_observer()
    observe = pad.add_probe.call_args.args[1]
    source = {"kind": "program", "event_id": "actual", "asset_id": "a"}
    structure = Mock()
    structure.get_name.return_value = "civiccast-source"
    structure.get_string.return_value = json.dumps(source)
    event = Mock()
    event.get_structure.return_value = structure
    observe(pad, types.SimpleNamespace(type=1, get_event=lambda: event))
    engine._select_caption_source.assert_not_called()
    observe(pad, types.SimpleNamespace(type=2))
    engine.selector.get_property.assert_not_called()
    engine._select_caption_source.assert_called_once_with(source, 123)
    pending_time, pending_source = engine._source_output_pending[0]
    assert pending_time == 123
    assert pending_source.items() >= source.items()
    assert pending_source["media_pts_seconds"] == 3600 + 123 / gst.SECOND
    assert abs(pending_source["media_wall_seconds"] - engine_module.time.time()) < 1
    # A sticky source identity replayed after switching away and back must
    # describe the returned program, rather than the last selected role.
    for selected in ({"kind": "slate", "event_id": "slate"}, source):
        structure.get_string.return_value = json.dumps(selected)
        observe(pad, types.SimpleNamespace(type=1, get_event=lambda: event))
        observe(pad, types.SimpleNamespace(type=2))
        assert engine._select_caption_source.call_args.args == (selected, 123)
    assert engine._select_caption_source.call_count == 3
    engine.selector.get_property.assert_not_called()


def test_source_role_marker_yields_to_rich_identity_before_same_frame(engine_module) -> None:
    gst = engine_module.Gst
    gst.PadProbeType = types.SimpleNamespace(EVENT_DOWNSTREAM=1, BUFFER=2)
    gst.PadProbeReturn = types.SimpleNamespace(OK=0)
    gst.EventType = types.SimpleNamespace(SEGMENT=3, CUSTOM_DOWNSTREAM_STICKY=4)
    structure = Mock()
    gst.Structure = types.SimpleNamespace(new_empty=Mock(return_value=structure))
    gst.Event = types.SimpleNamespace(new_custom=Mock(return_value="role-event"))
    pad = Mock()
    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine._install_source_role_marker(pad, "slate")
    mark = pad.add_probe.call_args.args[1]
    segment = types.SimpleNamespace(type=3, get_structure=lambda: None)
    mark(pad, types.SimpleNamespace(type=1, get_event=lambda: segment))
    mark(pad, types.SimpleNamespace(type=2))
    pad.push_event.assert_called_once_with("role-event")
    gst.Event.new_custom.assert_called_once_with(4, structure)
    source = json.loads(structure.set_value.call_args.args[1])
    assert source["kind"] == "slate"
    assert source["event_id"]
    pad.push_event.reset_mock()
    mark(pad, types.SimpleNamespace(type=1, get_event=lambda: segment))
    rich = types.SimpleNamespace(get_name=lambda: "civiccast-source")
    event = types.SimpleNamespace(type=4, get_structure=lambda: rich)
    mark(pad, types.SimpleNamespace(type=1, get_event=lambda: event))
    mark(pad, types.SimpleNamespace(type=2))
    pad.push_event.assert_not_called()
